//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"html/template"
	"log"
	"net/http"
	"sync"
	"time"
)

// TUI represents the terminal-style UI manager
type TUI struct {
	db       *sql.DB
	hub      *Hub
	queue    TaskQueue
	template *template.Template
	mu       sync.RWMutex
}

// TUIDashboard is the main dashboard data
type TUIDashboard struct {
	Agents    []TIAgent  `json:"agents"`
	Tasks     []TUITask  `json:"tasks"`
	Events    []TUIEvent `json:"events"`
	Metrics   TUIMetrics `json:"metrics"`
	Timestamp time.Time  `json:"timestamp"`
}

// TIAgent represents agent info for TUI
type TIAgent struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Node        string    `json:"node"`
	Role        string    `json:"role"`
	Status      string    `json:"status"`
	ContextPct  float64   `json:"context_pct"`
	CurrentTask string    `json:"current_task_id"`
	LastActive  time.Time `json:"last_activity"`
}

// TUITask represents task info for TUI
type TUITask struct {
	ID         string    `json:"id"`
	Domain     string    `json:"domain"`
	Project    string    `json:"project"`
	Type       string    `json:"type"`
	Status     string    `json:"status"`
	Priority   int       `json:"priority"`
	AssignedTo string    `json:"assigned_to"`
	CreatedAt  time.Time `json:"created_at"`
	UpdatedAt  time.Time `json:"updated_at"`
}

// TUIEvent represents event info for TUI
type TUIEvent struct {
	ID        string    `json:"id"`
	TaskID    string    `json:"task_id"`
	Domain    string    `json:"domain"`
	EventType string    `json:"event_type"`
	CreatedAt time.Time `json:"created_at"`
}

// TUIMetrics represents system metrics
type TUIMetrics struct {
	TotalTasks     int `json:"total_tasks"`
	PendingTasks   int `json:"pending_tasks"`
	RunningTasks   int `json:"running_tasks"`
	CompletedTasks int `json:"completed_tasks"`
	TotalAgents    int `json:"total_agents"`
	ActiveAgents   int `json:"active_agents"`
}

// NewTUI creates a new TUI instance
func NewTUI(db *sql.DB, hub *Hub, queue TaskQueue) *TUI {
	t := &TUI{
		db:    db,
		hub:   hub,
		queue: queue,
	}
	t.loadTemplate()
	return t
}

func (t *TUI) loadTemplate() {
	tmpl := `
<!DOCTYPE html>
<html>
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title>FORGE v3 TUI</title>
	<style>
		* { margin: 0; padding: 0; box-sizing: border-box; }
		body {
			background: #0d1117;
			color: #c9d1d9;
			font-family: 'SF Mono', 'Monaco', 'Menlo', monospace;
			font-size: 12px;
			padding: 20px;
		}
		.header {
			display: flex;
			justify-content: space-between;
			align-items: center;
			margin-bottom: 20px;
			padding-bottom: 10px;
			border-bottom: 1px solid #30363d;
		}
		.header h1 { color: #58a6ff; font-size: 18px; }
		.header .timestamp { color: #8b949e; }
		.grid {
			display: grid;
			grid-template-columns: 1fr 1fr;
			gap: 20px;
		}
		.panel {
			background: #161b22;
			border: 1px solid #30363d;
			border-radius: 6px;
			padding: 15px;
		}
		.panel h2 {
			color: #58a6ff;
			font-size: 14px;
			margin-bottom: 10px;
			padding-bottom: 5px;
			border-bottom: 1px solid #30363d;
		}
		.metrics {
			display: grid;
			grid-template-columns: repeat(5, 1fr);
			gap: 10px;
			margin-bottom: 20px;
		}
		.metric {
			background: #161b22;
			border: 1px solid #30363d;
			border-radius: 6px;
			padding: 15px;
			text-align: center;
		}
		.metric .value { font-size: 24px; font-weight: bold; }
		.metric .label { color: #8b949e; margin-top: 5px; }
		.metric.total .value { color: #58a6ff; }
		.metric.pending .value { color: #d29922; }
		.metric.running .value { color: #3fb950; }
		.metric.completed .value { color: #a371f7; }
		.metric.agents .value { color: #f778ba; }
		.agent { padding: 8px; margin: 5px 0; background: #21262d; border-radius: 4px; }
		.agent .name { color: #58a6ff; font-weight: bold; }
		.agent .status { float: right; }
		.agent .status.online { color: #3fb950; }
		.agent .status.offline { color: #f85149; }
		.agent .status.busy { color: #d29922; }
		.agent .details { color: #8b949e; margin-top: 3px; font-size: 11px; }
		.task { padding: 8px; margin: 5px 0; background: #21262d; border-radius: 4px; }
		.task .id { color: #58a6ff; font-weight: bold; }
		.task .status { float: right; }
		.task .status.requested { color: #d29922; }
		.task .status.queued { color: #58a6ff; }
		.task .status.assigned { color: #3fb950; }
		.task .status.completed { color: #a371f7; }
		.task .status.failed { color: #f85149; }
		.task .details { color: #8b949e; margin-top: 3px; font-size: 11px; }
		.event { padding: 5px; margin: 3px 0; border-left: 2px solid #30363d; }
		.event .type { color: #8b949e; }
		.event .task { color: #58a6ff; }
		.event .time { float: right; color: #484f58; }
		.logs {
			background: #0d1117;
			border: 1px solid #30363d;
			border-radius: 6px;
			padding: 10px;
			height: 300px;
			overflow-y: auto;
			font-family: 'SF Mono', monospace;
		}
		.logs .entry { margin: 2px 0; }
		.logs .entry .time { color: #484f58; }
		.logs .entry .msg { color: #c9d1d9; }
		.empty { color: #484f58; text-align: center; padding: 20px; }
		.refresh { margin-top: 20px; text-align: center; }
		.refresh button {
			background: #238636;
			color: white;
			border: none;
			padding: 10px 20px;
			border-radius: 6px;
			cursor: pointer;
			font-family: monospace;
		}
		.refresh button:hover { background: #2ea043; }
		.notice {
			margin-bottom: 16px;
			padding: 12px 14px;
			border: 1px solid #30363d;
			border-radius: 6px;
			background: #111827;
			color: #8b949e;
		}
		.notice a {
			color: #58a6ff;
			text-decoration: none;
		}
	</style>
</head>
<body>
	<div class="header">
		<h1>▌ FORGE v3 TUI</h1>
		<span class="timestamp">{{ .Timestamp.Format "2006-01-02 15:04:05" }}</span>
	</div>
	<div class="notice">Preferred browser UI: <a href="/ui">/ui</a>. This page is a compatibility and debugging view.</div>

	<div class="metrics">
		<div class="metric total">
			<div class="value">{{ .Metrics.TotalTasks }}</div>
			<div class="label">Total Tasks</div>
		</div>
		<div class="metric pending">
			<div class="value">{{ .Metrics.PendingTasks }}</div>
			<div class="label">Pending</div>
		</div>
		<div class="metric running">
			<div class="value">{{ .Metrics.RunningTasks }}</div>
			<div class="label">Running</div>
		</div>
		<div class="metric completed">
			<div class="value">{{ .Metrics.CompletedTasks }}</div>
			<div class="label">Completed</div>
		</div>
		<div class="metric agents">
			<div class="value">{{ .Metrics.ActiveAgents }}/{{ .Metrics.TotalAgents }}</div>
			<div class="label">Agents</div>
		</div>
	</div>

	<div class="grid">
		<div class="panel">
			<h2>▸ Agents</h2>
			{{ range .Agents }}
			<div class="agent">
				<span class="name">{{ .Name }}</span>
				<span class="status {{ .Status }}">{{ .Status }}</span>
				<div class="details">
					{{ .Node }} | {{ .Role }} | {{ .ContextPct }}% ctx
					{{ if .CurrentTask }} | Task: {{ .CurrentTask }}{{ end }}
				</div>
			</div>
			{{ else }}
			<div class="empty">No agents connected</div>
			{{ end }}
		</div>

		<div class="panel">
			<h2>▸ Tasks</h2>
			{{ range .Tasks }}
			<div class="task">
				<span class="id">{{ .ID }}</span>
				<span class="status {{ .Status }}">{{ .Status }}</span>
				<div class="details">
					{{ .Domain }}/{{ .Project }} | {{ .Type }}
					{{ if .AssignedTo }} | Assigned: {{ .AssignedTo }}{{ end }}
				</div>
			</div>
			{{ else }}
			<div class="empty">No tasks</div>
			{{ end }}
		</div>
	</div>

	<div class="panel" style="margin-top: 20px;">
		<h2>▸ Recent Events</h2>
		{{ range .Events }}
		<div class="event">
			<span class="type">{{ .EventType }}</span>
			<span class="task">{{ .Domain }}:{{ .TaskID }}</span>
			<span class="time">{{ .CreatedAt.Format "15:04:05" }}</span>
		</div>
		{{ else }}
		<div class="empty">No recent events</div>
		{{ end }}
	</div>

	<div class="refresh">
		<button onclick="location.reload()">⟳ Refresh</button>
	</div>

	<script>
		// Auto-refresh every 10 seconds
		setTimeout(() => location.reload(), 10000);
	</script>
</body>
</html>`

	t.template = template.Must(template.New("tui").Parse(tmpl))
}

// GetDashboard returns the current dashboard data
func (t *TUI) GetDashboard(ctx context.Context) (*TUIDashboard, error) {
	dash := &TUIDashboard{
		Timestamp: time.Now(),
	}

	// Get agents from database
	rows, err := t.db.QueryContext(ctx, `
		SELECT id, name, node, role, status, context_pct, current_task_id, last_activity
		FROM agents
		ORDER BY last_activity DESC
		LIMIT 20
	`)
	if err != nil {
		log.Printf("TUI: failed to get agents: %v", err)
	} else {
		defer rows.Close()
		for rows.Next() {
			var agent TIAgent
			var lastActivity sql.NullTime
			if err := rows.Scan(&agent.ID, &agent.Name, &agent.Node, &agent.Role,
				&agent.Status, &agent.ContextPct, &agent.CurrentTask, &lastActivity); err == nil {
				if lastActivity.Valid {
					agent.LastActive = lastActivity.Time
				}
				dash.Agents = append(dash.Agents, agent)
			}
		}
	}

	// Get tasks from queue
	tasks, err := t.queue.ListAllTasks(ctx, 20)
	if err != nil {
		log.Printf("TUI: failed to get tasks: %v", err)
	} else {
		for _, task := range tasks {
			dash.Tasks = append(dash.Tasks, TUITask{
				ID:         task.ID,
				Domain:     task.Domain,
				Project:    task.Project,
				Type:       string(task.Type),
				Status:     string(task.Status),
				Priority:   task.Priority,
				AssignedTo: task.AssignedTo,
				CreatedAt:  task.CreatedAt,
				UpdatedAt:  task.UpdatedAt,
			})
		}
	}

	// Get recent events
	eventRows, err := t.db.QueryContext(ctx, `
		SELECT id, task_id, domain, event_type, created_at
		FROM task_events
		ORDER BY created_at DESC
		LIMIT 30
	`)
	if err != nil {
		log.Printf("TUI: failed to get events: %v", err)
	} else {
		defer eventRows.Close()
		for eventRows.Next() {
			var event TUIEvent
			if err := eventRows.Scan(&event.ID, &event.TaskID, &event.Domain,
				&event.EventType, &event.CreatedAt); err == nil {
				dash.Events = append(dash.Events, event)
			}
		}
	}

	// Calculate metrics
	dash.Metrics.TotalAgents = len(dash.Agents)
	dash.Metrics.ActiveAgents = 0
	for _, a := range dash.Agents {
		if a.Status == "online" || a.Status == "busy" {
			dash.Metrics.ActiveAgents++
		}
	}
	dash.Metrics.TotalTasks = len(dash.Tasks)
	for _, task := range dash.Tasks {
		switch task.Status {
		case "requested", "queued":
			dash.Metrics.PendingTasks++
		case "assigned", "planned":
			dash.Metrics.RunningTasks++
		case "completed":
			dash.Metrics.CompletedTasks++
		}
	}

	return dash, nil
}

// Handler returns an HTTP handler for the TUI
func (t *TUI) Handler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		dash, err := t.GetDashboard(r.Context())
		if err != nil {
			http.Error(w, fmt.Sprintf("Failed to get dashboard: %v", err), http.StatusInternalServerError)
			return
		}

		w.Header().Set("Content-Type", "text/html")
		if err := t.template.Execute(w, dash); err != nil {
			log.Printf("TUI: template error: %v", err)
		}
	}
}

// JSONHandler returns JSON data for the TUI
func (t *TUI) JSONHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		dash, err := t.GetDashboard(r.Context())
		if err != nil {
			http.Error(w, fmt.Sprintf("Failed to get dashboard: %v", err), http.StatusInternalServerError)
			return
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(dash)
	}
}
