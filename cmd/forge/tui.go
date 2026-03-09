package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os/exec"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/charmbracelet/bubbles/key"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/spf13/cobra"
)

// tuiTab identifies the active view.
type tuiTab int

const (
	tuiTabOverview    tuiTab = 0
	tuiTabAgents      tuiTab = 1
	tuiTabTasks       tuiTab = 2
	tuiTabApprovals   tuiTab = 3
	tuiTabSprints     tuiTab = 4
	tuiTabLogs        tuiTab = 5
	tuiTabAgentDetail tuiTab = 6
	tuiTabCount       tuiTab = 7
)

type tuiPatrolEntry struct{ Name, Status, LastRun string; Errors int }

type tuiState struct {
	tasks     []internal.Task
	agents    []internal.Agent
	approvals []internal.Approval
	patrols   []tuiPatrolEntry
	sprints   string
	logs      []string
	daemonUp  bool
	lastErr   string
	updatedAt time.Time
}

type tuiModel struct {
	tab            tuiTab
	state          tuiState
	cursor         int
	width, height  int
	apiURL         string
	quitting       bool
	dispatchMode   bool
	dispatchBuf    string
	dispatchStatus string
	selectedAgent  string
}

type tuiTickMsg time.Time
type tuiDataMsg tuiState
type tuiDispatchDoneMsg string

// Styles
var (
	tuiTitleS   = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("#58a6ff")).PaddingLeft(1)
	tuiActiveT  = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("#ffffff")).Background(lipgloss.Color("#1f6feb")).Padding(0, 1)
	tuiInactiveT = lipgloss.NewStyle().Foreground(lipgloss.Color("#8b949e")).Padding(0, 1)
	tuiHeadS    = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("#58a6ff"))
	tuiGreenS   = lipgloss.NewStyle().Foreground(lipgloss.Color("#3fb950"))
	tuiRedS     = lipgloss.NewStyle().Foreground(lipgloss.Color("#f85149"))
	tuiYellowS  = lipgloss.NewStyle().Foreground(lipgloss.Color("#d29922"))
	tuiGrayS    = lipgloss.NewStyle().Foreground(lipgloss.Color("#8b949e"))
	tuiBorderS  = lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(lipgloss.Color("#30363d")).Padding(0, 1)
	tuiStatusS  = lipgloss.NewStyle().Background(lipgloss.Color("#161b22")).Foreground(lipgloss.Color("#8b949e")).PaddingLeft(1).PaddingRight(1)
	tuiOverlayS = lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(lipgloss.Color("#d29922")).Padding(1, 2)
)

var tuiKeys = struct {
	quit, tab, shiftTab                    key.Binding
	t1, t2, t3, t4, t5, t6, t7            key.Binding
	refresh, up, down, approve, reject     key.Binding
	theme, dispatch, enter, esc            key.Binding
}{
	quit:     key.NewBinding(key.WithKeys("q", "ctrl+c")),
	tab:      key.NewBinding(key.WithKeys("tab")),
	shiftTab: key.NewBinding(key.WithKeys("shift+tab")),
	t1: key.NewBinding(key.WithKeys("1")), t2: key.NewBinding(key.WithKeys("2")),
	t3: key.NewBinding(key.WithKeys("3")), t4: key.NewBinding(key.WithKeys("4")),
	t5: key.NewBinding(key.WithKeys("5")), t6: key.NewBinding(key.WithKeys("6")),
	t7:       key.NewBinding(key.WithKeys("7")),
	refresh:  key.NewBinding(key.WithKeys("r")),
	up:       key.NewBinding(key.WithKeys("up", "k")),
	down:     key.NewBinding(key.WithKeys("down", "j")),
	approve:  key.NewBinding(key.WithKeys("a")),
	reject:   key.NewBinding(key.WithKeys("x")),
	theme:    key.NewBinding(key.WithKeys("t")),
	dispatch: key.NewBinding(key.WithKeys("d")),
	enter:    key.NewBinding(key.WithKeys("enter")),
	esc:      key.NewBinding(key.WithKeys("esc")),
}

func newTUIModel(apiURL string) tuiModel { return tuiModel{apiURL: apiURL} }

func (m tuiModel) Init() tea.Cmd { return tea.Batch(m.fetchCmd(), m.tickCmd()) }

func (m tuiModel) tickCmd() tea.Cmd {
	return tea.Tick(3*time.Second, func(t time.Time) tea.Msg { return tuiTickMsg(t) })
}

func (m tuiModel) fetchCmd() tea.Cmd {
	apiURL := m.apiURL
	return func() tea.Msg {
		st := tuiState{updatedAt: time.Now()}
		ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
		defer cancel()
		client := internal.NewClientWithURL(apiURL)

		resp, err := client.Get(ctx, "/health")
		if err != nil || resp.StatusCode >= 500 {
			if resp != nil { resp.Body.Close() }
			st.lastErr = "daemon unreachable — run: forge daemon start"
			return tuiDataMsg(st)
		}
		resp.Body.Close()
		st.daemonUp = true

		if tr, err := client.ListTasks(ctx, "", 100, ""); err == nil && tr != nil { st.tasks = tr.Tasks }
		if ar, err := client.ListAgents(ctx); err == nil && ar != nil { st.agents = ar.Agents }
		if appr, err := client.ListApprovals(ctx, "pending"); err == nil { st.approvals = appr }

		if pr, err := client.ListPatrols(ctx); err == nil {
			for _, p := range pr {
				lr := "-"
				if p.LastRun != nil { lr = *p.LastRun }
				st.patrols = append(st.patrols, tuiPatrolEntry{p.Name, p.Status, lr, p.ErrorCount})
			}
		}

		httpC := &http.Client{Timeout: 3 * time.Second}
		if dresp, err := httpC.Get(apiURL + "/dashboard"); err == nil {
			body, _ := io.ReadAll(dresp.Body); dresp.Body.Close()
			if dresp.StatusCode == http.StatusOK { st.sprints = tuiFormatDashboard(body) } else { st.sprints = "Sprint data not available" }
		} else { st.sprints = "Sprint data not available" }

		if eresp, err := httpC.Get(apiURL + "/events"); err == nil && eresp.StatusCode == http.StatusOK {
			body, _ := io.ReadAll(eresp.Body); eresp.Body.Close()
			st.logs = tuiParseEventLog(body)
		} else {
			if eresp != nil { eresp.Body.Close() }
			for _, p := range st.patrols {
				st.logs = append(st.logs, fmt.Sprintf("[patrol] %-28s status=%-8s last=%s err=%d", p.Name, p.Status, p.LastRun, p.Errors))
			}
			if len(st.logs) == 0 { st.logs = []string{"No event log available"} }
		}
		return tuiDataMsg(st)
	}
}

func (m tuiModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	if m.dispatchMode {
		switch msg := msg.(type) {
		case tuiDispatchDoneMsg:
			m.dispatchMode = false; m.dispatchStatus = string(msg)
		case tea.KeyMsg:
			switch {
			case key.Matches(msg, tuiKeys.esc):
				m.dispatchMode = false; m.dispatchBuf = ""; m.dispatchStatus = ""
			case key.Matches(msg, tuiKeys.enter):
				if v := strings.TrimSpace(m.dispatchBuf); v != "" { return m, m.dispatchSendCmd(v) }
				m.dispatchMode = false; m.dispatchBuf = ""
			default:
				k := msg.String()
				if k == "backspace" || k == "ctrl+h" {
					if len(m.dispatchBuf) > 0 { m.dispatchBuf = m.dispatchBuf[:len(m.dispatchBuf)-1] }
				} else if k == "space" { m.dispatchBuf += " "
				} else if len(k) == 1 { m.dispatchBuf += k }
			}
		}
		return m, nil
	}

	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
	case tea.KeyMsg:
		switch {
		case key.Matches(msg, tuiKeys.quit): m.quitting = true; return m, tea.Quit
		case key.Matches(msg, tuiKeys.tab): m.tab = (m.tab + 1) % tuiTabCount; m.cursor = 0
		case key.Matches(msg, tuiKeys.shiftTab): m.tab = (m.tab + tuiTabCount - 1) % tuiTabCount; m.cursor = 0
		case key.Matches(msg, tuiKeys.t1): m.tab, m.cursor = tuiTabOverview, 0
		case key.Matches(msg, tuiKeys.t2): m.tab, m.cursor = tuiTabAgents, 0
		case key.Matches(msg, tuiKeys.t3): m.tab, m.cursor = tuiTabTasks, 0
		case key.Matches(msg, tuiKeys.t4): m.tab, m.cursor = tuiTabApprovals, 0
		case key.Matches(msg, tuiKeys.t5): m.tab, m.cursor = tuiTabSprints, 0
		case key.Matches(msg, tuiKeys.t6): m.tab, m.cursor = tuiTabLogs, 0
		case key.Matches(msg, tuiKeys.t7): m.tab, m.cursor = tuiTabAgentDetail, 0
		case key.Matches(msg, tuiKeys.refresh): return m, m.fetchCmd()
		case key.Matches(msg, tuiKeys.up):
			if m.cursor > 0 { m.cursor-- }
		case key.Matches(msg, tuiKeys.down):
			if max := m.maxCursor(); m.cursor < max-1 { m.cursor++ }
		case key.Matches(msg, tuiKeys.approve):
			if m.tab == tuiTabApprovals && m.cursor < len(m.state.approvals) {
				id := m.state.approvals[m.cursor].ID
				return m, tea.Batch(m.approveCmd(id), m.fetchCmd())
			}
		case key.Matches(msg, tuiKeys.reject):
			if m.tab == tuiTabApprovals && m.cursor < len(m.state.approvals) {
				id := m.state.approvals[m.cursor].ID
				return m, tea.Batch(m.rejectCmd(id), m.fetchCmd())
			}
		case key.Matches(msg, tuiKeys.dispatch): m.dispatchMode = true; m.dispatchBuf = ""
		case key.Matches(msg, tuiKeys.enter):
			if m.tab == tuiTabAgents && m.cursor < len(m.state.agents) {
				m.selectedAgent = m.state.agents[m.cursor].ID; m.tab = tuiTabAgentDetail; m.cursor = 0
			}
		}
	case tuiTickMsg: return m, tea.Batch(m.fetchCmd(), m.tickCmd())
	case tuiDataMsg:
		m.state = tuiState(msg)
		if max := m.maxCursor(); max > 0 && m.cursor >= max { m.cursor = max - 1 }
	}
	return m, nil
}

func (m tuiModel) maxCursor() int {
	switch m.tab {
	case tuiTabAgents: return len(m.state.agents)
	case tuiTabTasks: return len(m.state.tasks)
	case tuiTabApprovals: return len(m.state.approvals)
	case tuiTabLogs: return len(m.state.logs)
	}
	return 0
}

func (m tuiModel) approveCmd(id string) tea.Cmd {
	u := m.apiURL
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second); defer cancel()
		_ = internal.NewClientWithURL(u).ApproveApproval(ctx, id, "forge-tui"); return nil
	}
}
func (m tuiModel) rejectCmd(id string) tea.Cmd {
	u := m.apiURL
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second); defer cancel()
		_ = internal.NewClientWithURL(u).RejectApproval(ctx, id, "forge-tui"); return nil
	}
}
func (m tuiModel) dispatchSendCmd(val string) tea.Cmd {
	return func() tea.Msg {
		parts := strings.SplitN(val, " ", 2)
		agent, msg2 := parts[0], ""
		if len(parts) > 1 { msg2 = parts[1] }
		out, err := exec.Command("forge", "dispatch", "send", "forge:"+agent, msg2).CombinedOutput()
		if err != nil { return tuiDispatchDoneMsg("error: " + strings.TrimSpace(string(out))) }
		return tuiDispatchDoneMsg("dispatched to " + agent)
	}
}

func (m tuiModel) View() string {
	if m.width == 0 { return "Loading..." }
	if m.dispatchMode {
		title := tuiHeadS.Render("Quick Dispatch")
		hint := tuiGrayS.Render("Format: <agent> <message>  |  Enter=send  Esc=cancel")
		input := tuiGrayS.Render("> ") + m.dispatchBuf + tuiYellowS.Render("_")
		status := ""
		if m.dispatchStatus != "" { status = "\n" + tuiGrayS.Render(m.dispatchStatus) }
		overlay := tuiOverlayS.Width(60).Render(lipgloss.JoinVertical(lipgloss.Left, title, hint, "", input, status))
		return lipgloss.Place(m.width, m.height, lipgloss.Center, lipgloss.Center, overlay,
			lipgloss.WithWhitespaceBackground(lipgloss.Color("#0d1117")))
	}

	contentH := m.height - 6
	if contentH < 1 { contentH = 1 }
	var content string
	switch m.tab {
	case tuiTabOverview: content = m.renderOverview(contentH)
	case tuiTabAgents: content = m.renderAgents(contentH)
	case tuiTabTasks: content = m.renderTasks(contentH)
	case tuiTabApprovals: content = m.renderApprovals(contentH)
	case tuiTabSprints: content = m.renderSprints(contentH)
	case tuiTabLogs: content = m.renderLogs(contentH)
	case tuiTabAgentDetail: content = m.renderAgentDetail(contentH)
	}

	// Header
	daemon := tuiRedS.Render("daemon DOWN")
	if m.state.daemonUp { daemon = tuiGreenS.Render("daemon UP") }
	updated := ""
	if !m.state.updatedAt.IsZero() { updated = tuiGrayS.Render(" " + m.state.updatedAt.Format("15:04:05")) }
	title := tuiTitleS.Render("FORGE TUI")
	rw := m.width - lipgloss.Width(title) - 2
	header := lipgloss.JoinHorizontal(lipgloss.Top, title,
		lipgloss.NewStyle().Width(rw).Align(lipgloss.Right).Render(daemon+updated))

	// Tabs
	names := []string{"1:Overview", "2:Agents", "3:Tasks", "4:Approvals", "5:Sprints", "6:Logs", "7:Detail"}
	var parts []string
	for i, n := range names {
		if tuiTab(i) == m.tab { parts = append(parts, tuiActiveT.Render(n)) } else { parts = append(parts, tuiInactiveT.Render(n)) }
	}
	tabs := lipgloss.JoinHorizontal(lipgloss.Left, parts...)

	// Help bar
	help := "q:quit  tab/1-7:switch  r:refresh  j/k:move  d:dispatch"
	if m.tab == tuiTabApprovals { help += "  a:approve  x:reject" }
	if m.tab == tuiTabAgents { help += "  enter:detail" }

	return lipgloss.JoinVertical(lipgloss.Left, header, tabs, content, tuiStatusS.Width(m.width).Render(help))
}

func (m tuiModel) renderOverview(h int) string {
	if !m.state.daemonUp { return tuiBorderS.Width(m.width-2).Height(h).Render(tuiRedS.Render("Daemon unreachable\n"+m.state.lastErr)) }
	q, r, d, f := 0, 0, 0, 0
	for _, t := range m.state.tasks {
		switch t.Status {
		case "queued", "requested": q++
		case "assigned", "executing": r++
		case "completed": d++
		case "failed": f++
		}
	}
	online := 0
	for _, a := range m.state.agents { if a.Status == "online" || a.Status == "idle" || a.Status == "working" { online++ } }
	patrolRun := 0
	for _, p := range m.state.patrols { if p.Status == "running" { patrolRun++ } }
	lines := []string{
		tuiHeadS.Render("System Overview"), "",
		fmt.Sprintf("  Agents   : %s online / %d total", tuiGreenS.Render(fmt.Sprintf("%d", online)), len(m.state.agents)),
		fmt.Sprintf("  Tasks    : %s queued  %s running  %s done  %s failed",
			tuiYellowS.Render(fmt.Sprintf("%d", q)), tuiGreenS.Render(fmt.Sprintf("%d", r)),
			tuiGrayS.Render(fmt.Sprintf("%d", d)), tuiRedS.Render(fmt.Sprintf("%d", f))),
		fmt.Sprintf("  Approvals: %s pending", tuiYellowS.Render(fmt.Sprintf("%d", len(m.state.approvals)))),
		fmt.Sprintf("  Patrols  : %s running / %d total", tuiGreenS.Render(fmt.Sprintf("%d", patrolRun)), len(m.state.patrols)),
		fmt.Sprintf("  API URL  : %s", tuiGrayS.Render(m.apiURL)),
	}
	return tuiBorderS.Width(m.width - 2).Render(strings.Join(lines, "\n"))
}

func (m tuiModel) renderAgents(h int) string {
	if !m.state.daemonUp { return tuiBorderS.Width(m.width-2).Height(h).Render(tuiRedS.Render("Daemon unreachable")) }
	if len(m.state.agents) == 0 { return tuiBorderS.Width(m.width-2).Height(h).Render(tuiGrayS.Render("No agents connected")) }
	rows := []string{
		tuiHeadS.Render(tuiPad("NAME", 18) + tuiPad("STATUS", 10) + tuiPad("NODE", 14) + tuiPad("CTX%", 8) + "TASK"),
		tuiGrayS.Render(strings.Repeat("─", m.width-6)),
	}
	visH := h - 5
	if visH < 1 { visH = 1 }
	for i, a := range m.state.agents {
		if i >= visH { break }
		ss := tuiColorStatus(a.Status)
		task := "-"
		if a.CurrentTask != nil && *a.CurrentTask != "" { task = tuiTrunc(*a.CurrentTask, 20) }
		prefix := "  "
		if i == m.cursor { prefix = tuiYellowS.Render(">") }
		rows = append(rows, prefix+tuiPad(tuiTrunc(a.ID, 16), 16)+
			tuiPadC(ss, a.Status, 10)+tuiPad(tuiTrunc(a.Node, 12), 14)+
			tuiPad(fmt.Sprintf("%.0f%%", a.ContextPct), 8)+task)
	}
	rows = append(rows, tuiGrayS.Render("  [enter=detail]"))
	return tuiBorderS.Width(m.width - 2).Render(strings.Join(rows, "\n"))
}

func (m tuiModel) renderTasks(h int) string {
	if !m.state.daemonUp { return tuiBorderS.Width(m.width-2).Height(h).Render(tuiRedS.Render("Daemon unreachable")) }
	if len(m.state.tasks) == 0 { return tuiBorderS.Width(m.width-2).Height(h).Render(tuiGrayS.Render("No tasks in queue")) }
	rows := []string{
		tuiHeadS.Render(tuiPad("ID", 14) + tuiPad("PRIO", 9) + tuiPad("STATUS", 11) + tuiPad("AGENT", 14) + "TITLE"),
		tuiGrayS.Render(strings.Repeat("─", m.width-6)),
	}
	visH := h - 5
	if visH < 1 { visH = 1 }
	for i, t := range m.state.tasks {
		if i >= visH { break }
		ss := tuiColorTaskStatus(t.Status)
		agent := "-"
		if t.AssignedAgent != nil && *t.AssignedAgent != "" { agent = tuiTrunc(*t.AssignedAgent, 12) } else if t.AssignedTo != "" { agent = tuiTrunc(t.AssignedTo, 12) }
		prefix := "  "
		if i == m.cursor { prefix = tuiYellowS.Render(">") }
		titleW := m.width - 14 - 9 - 11 - 14 - 10
		if titleW < 10 { titleW = 10 }
		rows = append(rows, prefix+tuiPad(tuiTrunc(t.ID, 12), 12)+
			tuiPad(internal.PriorityToLabel(t.Priority), 9)+
			tuiPadC(ss, t.Status, 11)+tuiPad(agent, 14)+tuiTrunc(t.DisplayTitle(), titleW))
	}
	return tuiBorderS.Width(m.width - 2).Render(strings.Join(rows, "\n"))
}

func (m tuiModel) renderApprovals(h int) string {
	if !m.state.daemonUp { return tuiBorderS.Width(m.width-2).Height(h).Render(tuiRedS.Render("Daemon unreachable")) }
	if len(m.state.approvals) == 0 { return tuiBorderS.Width(m.width-2).Height(h).Render(tuiGreenS.Render("No pending approvals")) }
	rows := []string{
		tuiHeadS.Render(tuiPad("ID", 14) + tuiPad("TYPE", 10) + tuiPad("DOMAIN", 16) + tuiPad("RISK", 6) + "TITLE"),
		tuiGrayS.Render(strings.Repeat("─", m.width-6)),
	}
	visH := h - 5
	if visH < 1 { visH = 1 }
	for i, a := range m.state.approvals {
		if i >= visH { break }
		prefix, sfx := "  ", ""
		if i == m.cursor { prefix = tuiYellowS.Render(">"); sfx = tuiYellowS.Render(" [a=approve x=reject]") }
		titleW := m.width - 14 - 10 - 16 - 6 - 14
		if titleW < 10 { titleW = 10 }
		rows = append(rows, prefix+tuiPad(tuiTrunc(a.ID, 12), 14)+tuiPad(a.Type, 10)+
			tuiPad(tuiTrunc(a.Domain, 14), 16)+tuiPad(fmt.Sprintf("%.2f", a.RiskScore), 6)+
			tuiTrunc(a.Title, titleW)+sfx)
	}
	return tuiBorderS.Width(m.width - 2).Render(strings.Join(rows, "\n"))
}

func (m tuiModel) renderSprints(h int) string {
	if !m.state.daemonUp { return tuiBorderS.Width(m.width-2).Height(h).Render(tuiRedS.Render("Daemon unreachable")) }
	lines := strings.Split(m.state.sprints, "\n")
	visH := h - 3
	if visH < 1 { visH = 1 }
	start := m.cursor
	if start > len(lines)-visH { start = len(lines) - visH }
	if start < 0 { start = 0 }
	vis := lines[start:]
	if len(vis) > visH { vis = vis[:visH] }
	return tuiBorderS.Width(m.width - 2).Render(tuiHeadS.Render("Sprints / Dashboard") + "\n\n" + strings.Join(vis, "\n"))
}

func (m tuiModel) renderLogs(h int) string {
	if !m.state.daemonUp { return tuiBorderS.Width(m.width-2).Height(h).Render(tuiRedS.Render("Daemon unreachable")) }
	logs := m.state.logs
	if len(logs) == 0 { logs = []string{"No log entries"} }
	visH := h - 3
	if visH < 1 { visH = 1 }
	start := m.cursor
	if start > len(logs)-visH { start = len(logs) - visH }
	if start < 0 { start = 0 }
	vis := logs[start:]
	if len(vis) > visH { vis = vis[:visH] }
	return tuiBorderS.Width(m.width - 2).Render(tuiHeadS.Render("Event Log") + "\n\n" + strings.Join(vis, "\n"))
}

func (m tuiModel) renderAgentDetail(h int) string {
	if !m.state.daemonUp { return tuiBorderS.Width(m.width-2).Height(h).Render(tuiRedS.Render("Daemon unreachable")) }
	var a *internal.Agent
	for i := range m.state.agents { if m.state.agents[i].ID == m.selectedAgent { a = &m.state.agents[i]; break } }
	if a == nil && len(m.state.agents) > 0 { a = &m.state.agents[0] }
	if a == nil { return tuiBorderS.Width(m.width-2).Height(h).Render(tuiGrayS.Render("No agent selected — go to Agents tab and press Enter")) }

	ctxS := tuiGreenS.Render(fmt.Sprintf("%.1f%%", a.ContextPct))
	if a.ContextPct > 75 { ctxS = tuiRedS.Render(fmt.Sprintf("%.1f%%", a.ContextPct)) } else if a.ContextPct > 50 { ctxS = tuiYellowS.Render(fmt.Sprintf("%.1f%%", a.ContextPct)) }
	task := "-"
	if a.CurrentTask != nil && *a.CurrentTask != "" { task = *a.CurrentTask }
	caps := strings.Join(a.Capabilities, ", ")
	if caps == "" { caps = "-" }
	taskCount := 0
	var recent []string
	for _, t := range m.state.tasks {
		ag := ""
		if t.AssignedAgent != nil { ag = *t.AssignedAgent }
		if ag == a.ID || t.AssignedTo == a.ID {
			taskCount++
			if len(recent) < 5 { recent = append(recent, fmt.Sprintf("  • [%s] %s", t.Status, tuiTrunc(t.DisplayTitle(), 55))) }
		}
	}
	lines := []string{
		tuiHeadS.Render("Agent: " + a.ID), "",
		fmt.Sprintf("  Status  : %s", tuiColorStatus(a.Status)),
		fmt.Sprintf("  Node    : %s", a.Node),
		fmt.Sprintf("  Context : %s", ctxS),
		fmt.Sprintf("  Task    : %s", task),
		fmt.Sprintf("  Caps    : %s", tuiGrayS.Render(caps)),
		fmt.Sprintf("  Seen    : %s", tuiGrayS.Render(a.LastPong.Format("2006-01-02 15:04:05"))),
		fmt.Sprintf("  Joined  : %s", tuiGrayS.Render(a.ConnectedAt.Format("2006-01-02 15:04:05"))),
		fmt.Sprintf("  Tasks   : %d assigned", taskCount), "",
		tuiHeadS.Render("Recent:"),
	}
	if len(recent) == 0 { recent = []string{"  No tasks"} }
	return tuiBorderS.Width(m.width - 2).Render(strings.Join(append(lines, recent...), "\n"))
}

// --- Helpers ---

func tuiPad(s string, w int) string {
	if len(s) >= w { return s[:w] }
	return s + strings.Repeat(" ", w-len(s))
}
func tuiPadC(styled, plain string, w int) string {
	pad := w - len(plain)
	if pad < 0 { pad = 0 }
	return styled + strings.Repeat(" ", pad)
}
func tuiTrunc(s string, max int) string {
	if max <= 0 { return "" }
	if len(s) <= max { return s }
	if max <= 3 { return s[:max] }
	return s[:max-3] + "..."
}
func tuiColorStatus(s string) string {
	switch s {
	case "online", "idle": return tuiGreenS.Render(s)
	case "working": return tuiYellowS.Render(s)
	case "offline", "error": return tuiRedS.Render(s)
	}
	return tuiGrayS.Render(s)
}
func tuiColorTaskStatus(s string) string {
	switch s {
	case "queued", "requested": return tuiYellowS.Render(s)
	case "executing", "assigned": return tuiGreenS.Render(s)
	case "failed": return tuiRedS.Render(s)
	}
	return tuiGrayS.Render(s)
}
func tuiFormatDashboard(body []byte) string {
	var data map[string]interface{}
	if err := json.Unmarshal(body, &data); err != nil { return string(body) }
	var lines []string
	for k, v := range data { lines = append(lines, fmt.Sprintf("  %-24s : %v", k, v)) }
	if len(lines) == 0 { return "Sprint data returned empty" }
	return strings.Join(lines, "\n")
}
func tuiParseEventLog(body []byte) []string {
	tryParse := func(events []map[string]interface{}) []string {
		lines := make([]string, 0, len(events))
		for _, e := range events {
			ts, _ := e["timestamp"].(string)
			if ts == "" { ts, _ = e["created_at"].(string) }
			kind, _ := e["type"].(string)
			msg, _ := e["message"].(string)
			lines = append(lines, fmt.Sprintf("[%s] %-20s %s", ts, kind, msg))
		}
		return lines
	}
	var events []map[string]interface{}
	if err := json.Unmarshal(body, &events); err == nil { return tryParse(events) }
	var wrapped struct{ Events []map[string]interface{} `json:"events"` }
	if err := json.Unmarshal(body, &wrapped); err == nil && len(wrapped.Events) > 0 { return tryParse(wrapped.Events) }
	raw := strings.TrimSpace(string(body))
	if raw == "" { return []string{"No events"} }
	return strings.Split(raw, "\n")
}

var tuiCmd = &cobra.Command{
	Use:   "tui",
	Short: "Rich 7-view terminal dashboard (Overview/Agents/Tasks/Approvals/Sprints/Logs/Detail)",
	Long: `Open a rich terminal dashboard with 7 views, polling the FORGE daemon every 3 seconds.

Views:  1=Overview  2=Agents  3=Tasks  4=Approvals  5=Sprints  6=Logs  7=AgentDetail

Keyboard:
  q            quit
  tab/shift+tab  next/prev view
  1-7          jump to view
  j/k          move cursor
  a            approve (Approvals view)
  x            reject (Approvals view)
  enter        drill into agent (Agents view → AgentDetail)
  r            force refresh
  d            quick dispatch overlay (<agent> <message>)`,
	RunE: func(cmd *cobra.Command, args []string) error {
		p := tea.NewProgram(newTUIModel(internal.ResolveAPIBaseURL()), tea.WithAltScreen())
		_, err := p.Run()
		return err
	},
}

func init() { rootCmd.AddCommand(tuiCmd) }
