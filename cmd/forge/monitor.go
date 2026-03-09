package main

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/charmbracelet/bubbles/key"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/spf13/cobra"
)

// monitorTab identifies which tab is active in the TUI.
type monitorTab int

const (
	monitorTabOverview  monitorTab = 0
	monitorTabAgents    monitorTab = 1
	monitorTabTasks     monitorTab = 2
	monitorTabApprovals monitorTab = 3
)

// monitorState holds the data fetched from the daemon.
type monitorState struct {
	tasks     []internal.Task
	agents    []internal.Agent
	approvals []internal.Approval
	daemonUp  bool
	lastErr   string
	updatedAt time.Time
}

// monitorModel is the BubbleTea model for the live TUI.
type monitorModel struct {
	tab      monitorTab
	state    monitorState
	cursor   int
	width    int
	height   int
	apiURL   string
	quitting bool
}

// --- BubbleTea messages ---

type monitorTickMsg time.Time
type monitorDataMsg monitorState

// --- Styles ---

var (
	monTitleStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#58a6ff")).
			PaddingLeft(1)

	monActiveTab = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#ffffff")).
			Background(lipgloss.Color("#1f6feb")).
			Padding(0, 2)

	monInactiveTab = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#8b949e")).
			Padding(0, 2)

	monHeaderStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#58a6ff"))

	monGreenStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#3fb950"))

	monRedStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#f85149"))

	monYellowStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#d29922"))

	monGrayStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#8b949e"))

	monBorderStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("#30363d")).
			Padding(0, 1)

	monStatusBarStyle = lipgloss.NewStyle().
				Background(lipgloss.Color("#161b22")).
				Foreground(lipgloss.Color("#8b949e")).
				PaddingLeft(1).
				PaddingRight(1)
)

// --- KeyMap ---

var monitorKeys = struct {
	quit    key.Binding
	tab     key.Binding
	tab1    key.Binding
	tab2    key.Binding
	tab3    key.Binding
	tab4    key.Binding
	refresh key.Binding
	up      key.Binding
	down    key.Binding
	approve key.Binding
	reject  key.Binding
}{
	quit:    key.NewBinding(key.WithKeys("q", "ctrl+c")),
	tab:     key.NewBinding(key.WithKeys("tab")),
	tab1:    key.NewBinding(key.WithKeys("1")),
	tab2:    key.NewBinding(key.WithKeys("2")),
	tab3:    key.NewBinding(key.WithKeys("3")),
	tab4:    key.NewBinding(key.WithKeys("4")),
	refresh: key.NewBinding(key.WithKeys("r")),
	up:      key.NewBinding(key.WithKeys("up", "k")),
	down:    key.NewBinding(key.WithKeys("down", "j")),
	approve: key.NewBinding(key.WithKeys("a")),
	reject:  key.NewBinding(key.WithKeys("x")),
}

// --- Init / Update / View ---

func newMonitorModel(apiURL string) monitorModel {
	return monitorModel{
		apiURL: apiURL,
		tab:    monitorTabOverview,
	}
}

func (m monitorModel) Init() tea.Cmd {
	return tea.Batch(m.fetchCmd(), m.tickCmd())
}

func (m monitorModel) tickCmd() tea.Cmd {
	return tea.Tick(2*time.Second, func(t time.Time) tea.Msg {
		return monitorTickMsg(t)
	})
}

func (m monitorModel) fetchCmd() tea.Cmd {
	apiURL := m.apiURL
	return func() tea.Msg {
		st := monitorState{updatedAt: time.Now()}

		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()

		client := internal.NewClientWithURL(apiURL)

		// Health check
		resp, err := client.Get(ctx, "/health")
		if err != nil || resp.StatusCode >= 500 {
			if resp != nil {
				resp.Body.Close()
			}
			st.daemonUp = false
			st.lastErr = "daemon unreachable — run: forge daemon start"
			return monitorDataMsg(st)
		}
		resp.Body.Close()
		st.daemonUp = true

		// Tasks
		taskResp, err := client.ListTasks(ctx, "", 100, "")
		if err == nil && taskResp != nil {
			st.tasks = taskResp.Tasks
		}

		// Agents
		agentResp, err := client.ListAgents(ctx)
		if err == nil && agentResp != nil {
			st.agents = agentResp.Agents
		}

		// Approvals
		approvals, err := client.ListApprovals(ctx, "pending")
		if err == nil {
			st.approvals = approvals
		}

		return monitorDataMsg(st)
	}
}

func (m monitorModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height

	case tea.KeyMsg:
		switch {
		case key.Matches(msg, monitorKeys.quit):
			m.quitting = true
			return m, tea.Quit

		case key.Matches(msg, monitorKeys.tab):
			m.tab = (m.tab + 1) % 4
			m.cursor = 0

		case key.Matches(msg, monitorKeys.tab1):
			m.tab = monitorTabOverview
			m.cursor = 0

		case key.Matches(msg, monitorKeys.tab2):
			m.tab = monitorTabAgents
			m.cursor = 0

		case key.Matches(msg, monitorKeys.tab3):
			m.tab = monitorTabTasks
			m.cursor = 0

		case key.Matches(msg, monitorKeys.tab4):
			m.tab = monitorTabApprovals
			m.cursor = 0

		case key.Matches(msg, monitorKeys.refresh):
			return m, m.fetchCmd()

		case key.Matches(msg, monitorKeys.up):
			if m.cursor > 0 {
				m.cursor--
			}

		case key.Matches(msg, monitorKeys.down):
			m.cursor++

		case key.Matches(msg, monitorKeys.approve):
			if m.tab == monitorTabApprovals && len(m.state.approvals) > 0 && m.cursor < len(m.state.approvals) {
				approvalID := m.state.approvals[m.cursor].ID
				return m, tea.Batch(m.approveCmd(approvalID), m.fetchCmd())
			}

		case key.Matches(msg, monitorKeys.reject):
			if m.tab == monitorTabApprovals && len(m.state.approvals) > 0 && m.cursor < len(m.state.approvals) {
				approvalID := m.state.approvals[m.cursor].ID
				return m, tea.Batch(m.rejectCmd(approvalID), m.fetchCmd())
			}
		}

	case monitorTickMsg:
		return m, tea.Batch(m.fetchCmd(), m.tickCmd())

	case monitorDataMsg:
		m.state = monitorState(msg)
		// Clamp cursor to list bounds
		maxCursor := m.maxCursorForTab()
		if m.cursor >= maxCursor && maxCursor > 0 {
			m.cursor = maxCursor - 1
		}
	}

	return m, nil
}

func (m monitorModel) maxCursorForTab() int {
	switch m.tab {
	case monitorTabAgents:
		return len(m.state.agents)
	case monitorTabTasks:
		return len(m.state.tasks)
	case monitorTabApprovals:
		return len(m.state.approvals)
	}
	return 0
}

func (m monitorModel) approveCmd(id string) tea.Cmd {
	apiURL := m.apiURL
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		client := internal.NewClientWithURL(apiURL)
		_ = client.ApproveApproval(ctx, id, "monitor-tui")
		return nil
	}
}

func (m monitorModel) rejectCmd(id string) tea.Cmd {
	apiURL := m.apiURL
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		client := internal.NewClientWithURL(apiURL)
		_ = client.RejectApproval(ctx, id, "monitor-tui")
		return nil
	}
}

func (m monitorModel) View() string {
	if m.width == 0 {
		return "Loading..."
	}

	header := m.renderHeader()
	tabs := m.renderTabs()

	contentH := m.height - 6
	if contentH < 1 {
		contentH = 1
	}

	var content string
	switch m.tab {
	case monitorTabOverview:
		content = m.renderOverview(contentH)
	case monitorTabAgents:
		content = m.renderAgents(contentH)
	case monitorTabTasks:
		content = m.renderTasks(contentH)
	case monitorTabApprovals:
		content = m.renderApprovals(contentH)
	}

	help := m.renderHelp()

	return lipgloss.JoinVertical(lipgloss.Left,
		header,
		tabs,
		content,
		help,
	)
}

func (m monitorModel) renderHeader() string {
	left := monTitleStyle.Render("FORGE Monitor")

	daemonStatus := monRedStyle.Render("daemon DOWN")
	if m.state.daemonUp {
		daemonStatus = monGreenStyle.Render("daemon UP")
	}

	updated := ""
	if !m.state.updatedAt.IsZero() {
		updated = monGrayStyle.Render(" updated " + m.state.updatedAt.Format("15:04:05"))
	}

	rightW := m.width - lipgloss.Width(left) - 2
	right := lipgloss.NewStyle().Width(rightW).Align(lipgloss.Right).Render(
		daemonStatus + updated,
	)

	return lipgloss.JoinHorizontal(lipgloss.Top, left, right)
}

func (m monitorModel) renderTabs() string {
	tabs := []string{"1:Overview", "2:Agents", "3:Tasks", "4:Approvals"}
	var parts []string
	for i, t := range tabs {
		if monitorTab(i) == m.tab {
			parts = append(parts, monActiveTab.Render(t))
		} else {
			parts = append(parts, monInactiveTab.Render(t))
		}
	}
	return lipgloss.JoinHorizontal(lipgloss.Left, parts...)
}

func (m monitorModel) renderHelp() string {
	base := "q:quit  tab/1-4:switch  r:refresh  j/k:up/down"
	if m.tab == monitorTabApprovals {
		base += "  a:approve  x:reject"
	}
	return monStatusBarStyle.Width(m.width).Render(base)
}

func (m monitorModel) renderOverview(height int) string {
	if !m.state.daemonUp {
		msg := monRedStyle.Render("Daemon unreachable")
		if m.state.lastErr != "" {
			msg += "\n" + monGrayStyle.Render(m.state.lastErr)
		}
		return monBorderStyle.Width(m.width - 2).Height(height).Render(msg)
	}

	// Count tasks by status
	queued, running, done := 0, 0, 0
	for _, t := range m.state.tasks {
		switch t.Status {
		case "queued", "requested":
			queued++
		case "assigned", "executing":
			running++
		case "completed":
			done++
		}
	}

	online := 0
	for _, a := range m.state.agents {
		if a.Status == "online" || a.Status == "idle" || a.Status == "working" {
			online++
		}
	}

	pendingApprovals := len(m.state.approvals)

	lines := []string{
		monHeaderStyle.Render("System Overview"),
		"",
		fmt.Sprintf("  Agents      : %s / %d connected",
			monGreenStyle.Render(fmt.Sprintf("%d", online)), len(m.state.agents)),
		fmt.Sprintf("  Tasks       : %s queued  %s running  %s done",
			monYellowStyle.Render(fmt.Sprintf("%d", queued)),
			monGreenStyle.Render(fmt.Sprintf("%d", running)),
			monGrayStyle.Render(fmt.Sprintf("%d", done))),
		fmt.Sprintf("  Approvals   : %s pending",
			monYellowStyle.Render(fmt.Sprintf("%d", pendingApprovals))),
		fmt.Sprintf("  API URL     : %s", monGrayStyle.Render(m.apiURL)),
	}

	return monBorderStyle.Width(m.width - 2).Render(strings.Join(lines, "\n"))
}

func (m monitorModel) renderAgents(height int) string {
	if !m.state.daemonUp {
		return monBorderStyle.Width(m.width - 2).Height(height).Render(
			monRedStyle.Render("Daemon unreachable"),
		)
	}

	if len(m.state.agents) == 0 {
		return monBorderStyle.Width(m.width - 2).Height(height).Render(
			monGrayStyle.Render("No agents connected"),
		)
	}

	colW := []int{16, 10, 14, 10, 20}
	header := monHeaderStyle.Render(
		padCol("NAME", colW[0]) +
			padCol("STATUS", colW[1]) +
			padCol("NODE", colW[2]) +
			padCol("CTX%", colW[3]) +
			"CURRENT TASK",
	)
	sep := monGrayStyle.Render(strings.Repeat("─", m.width-6))

	var rows []string
	rows = append(rows, header, sep)

	visH := height - 4
	if visH < 1 {
		visH = 1
	}

	for i, a := range m.state.agents {
		if i >= visH {
			break
		}
		statusStr := a.Status
		statusStyled := monGrayStyle.Render(statusStr)
		switch statusStr {
		case "online", "idle":
			statusStyled = monGreenStyle.Render(statusStr)
		case "working":
			statusStyled = monYellowStyle.Render(statusStr)
		case "offline", "error":
			statusStyled = monRedStyle.Render(statusStr)
		}

		task := "-"
		if a.CurrentTask != nil && *a.CurrentTask != "" {
			task = truncateMonStr(*a.CurrentTask, 20)
		}

		ctxPct := fmt.Sprintf("%.0f%%", a.ContextPct)

		prefix := "  "
		if i == m.cursor {
			prefix = monActiveTab.Render(">")
		}

		row := prefix + padCol(truncateMonStr(a.ID, colW[0]-2), colW[0]-2) +
			padColorCol(statusStyled, statusStr, colW[1]) +
			padCol(truncateMonStr(a.Node, colW[2]-2), colW[2]) +
			padCol(ctxPct, colW[3]) +
			task

		rows = append(rows, row)
	}

	return monBorderStyle.Width(m.width - 2).Render(strings.Join(rows, "\n"))
}

func (m monitorModel) renderTasks(height int) string {
	if !m.state.daemonUp {
		return monBorderStyle.Width(m.width - 2).Height(height).Render(
			monRedStyle.Render("Daemon unreachable"),
		)
	}

	if len(m.state.tasks) == 0 {
		return monBorderStyle.Width(m.width - 2).Height(height).Render(
			monGrayStyle.Render("No tasks in queue"),
		)
	}

	colW := []int{14, 8, 10, 22}
	header := monHeaderStyle.Render(
		padCol("ID", colW[0]) +
			padCol("PRIORITY", colW[1]) +
			padCol("STATUS", colW[2]) +
			"TITLE",
	)
	sep := monGrayStyle.Render(strings.Repeat("─", m.width-6))

	var rows []string
	rows = append(rows, header, sep)

	visH := height - 4
	if visH < 1 {
		visH = 1
	}

	for i, t := range m.state.tasks {
		if i >= visH {
			break
		}

		statusStr := t.Status
		statusStyled := monGrayStyle.Render(statusStr)
		switch statusStr {
		case "queued", "requested":
			statusStyled = monYellowStyle.Render(statusStr)
		case "executing", "assigned":
			statusStyled = monGreenStyle.Render(statusStr)
		case "completed":
			statusStyled = monGrayStyle.Render(statusStr)
		case "failed":
			statusStyled = monRedStyle.Render(statusStr)
		}

		priStr := internal.PriorityToLabel(t.Priority)

		prefix := "  "
		if i == m.cursor {
			prefix = monActiveTab.Render(">")
		}

		titleW := m.width - colW[0] - colW[1] - colW[2] - 10
		if titleW < 10 {
			titleW = 10
		}

		row := prefix +
			padCol(truncateMonStr(t.ID, colW[0]-2), colW[0]-2) +
			padCol(priStr, colW[1]) +
			padColorCol(statusStyled, statusStr, colW[2]) +
			truncateMonStr(t.Title, titleW)

		rows = append(rows, row)
	}

	return monBorderStyle.Width(m.width - 2).Render(strings.Join(rows, "\n"))
}

func (m monitorModel) renderApprovals(height int) string {
	if !m.state.daemonUp {
		return monBorderStyle.Width(m.width - 2).Height(height).Render(
			monRedStyle.Render("Daemon unreachable"),
		)
	}

	if len(m.state.approvals) == 0 {
		return monBorderStyle.Width(m.width - 2).Height(height).Render(
			monGreenStyle.Render("No pending approvals"),
		)
	}

	header := monHeaderStyle.Render(
		padCol("ID", 14) +
			padCol("TYPE", 10) +
			padCol("DOMAIN", 20) +
			"TITLE",
	)
	sep := monGrayStyle.Render(strings.Repeat("─", m.width-6))

	var rows []string
	rows = append(rows, header, sep)

	visH := height - 5
	if visH < 1 {
		visH = 1
	}

	for i, a := range m.state.approvals {
		if i >= visH {
			break
		}

		prefix := "  "
		cursor := ""
		if i == m.cursor {
			prefix = monYellowStyle.Render(">")
			cursor = monYellowStyle.Render(" [a=approve x=reject]")
		}

		titleW := m.width - 14 - 10 - 20 - 12
		if titleW < 10 {
			titleW = 10
		}

		row := prefix +
			padCol(truncateMonStr(a.ID, 12), 14) +
			padCol(a.Type, 10) +
			padCol(truncateMonStr(a.Domain, 18), 20) +
			truncateMonStr(a.Title, titleW) +
			cursor

		rows = append(rows, row)
	}

	return monBorderStyle.Width(m.width - 2).Render(strings.Join(rows, "\n"))
}

// helpers

func padCol(s string, w int) string {
	if len(s) >= w {
		return s[:w]
	}
	return s + strings.Repeat(" ", w-len(s))
}

// padColorCol pads based on the plain-text width (not the styled string length).
func padColorCol(styled, plain string, w int) string {
	pad := w - len(plain)
	if pad < 0 {
		pad = 0
	}
	return styled + strings.Repeat(" ", pad)
}

func truncateMonStr(s string, max int) string {
	if max <= 0 {
		return ""
	}
	if len(s) <= max {
		return s
	}
	if max <= 3 {
		return s[:max]
	}
	return s[:max-3] + "..."
}

// monitorCmd: forge monitor — live TUI dashboard
var monitorCmd = &cobra.Command{
	Use:   "monitor",
	Short: "Live TUI dashboard for fleet and task monitoring",
	Long: `Open a live terminal dashboard that polls the FORGE daemon every 2 seconds.

Keyboard shortcuts:
  q         Quit
  tab       Next view
  1-4       Jump to view (1=Overview 2=Agents 3=Tasks 4=Approvals)
  r         Force refresh
  j/k       Move cursor up/down
  a         Approve selected (Approvals view)
  x         Reject selected (Approvals view)`,
	RunE: func(cmd *cobra.Command, args []string) error {
		apiURL := internal.ResolveAPIBaseURL()

		model := newMonitorModel(apiURL)
		p := tea.NewProgram(model, tea.WithAltScreen())
		_, err := p.Run()
		return err
	},
}

func init() {
	rootCmd.AddCommand(monitorCmd)
}
