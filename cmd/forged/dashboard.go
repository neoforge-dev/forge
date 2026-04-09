package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"html/template"
	"net/http"
	"sort"
	"sync"
	"time"
)

// AgentHealth represents the health status of an agent
type AgentHealth struct {
	AgentID         string            `json:"agent_id"`
	Node            string            `json:"node"`
	Status          AgentStatus       `json:"status"`
	StatusText      string            `json:"status_text"`
	ContextPercent  float64           `json:"context_percent"`
	CurrentTask     *string           `json:"current_task,omitempty"`
	TaskProgress    int               `json:"task_progress"` // 0-100
	TaskStatus      string            `json:"task_status,omitempty"`
	LastHeartbeat   time.Time         `json:"last_heartbeat"`
	LastActivity    time.Time         `json:"last_activity"`
	ConnectedAt     time.Time         `json:"connected_at"`
	Capabilities    []string          `json:"capabilities"`
	TaskCount       int               `json:"task_count"`
	SuccessRate     float64           `json:"success_rate"`
	AvgTaskDuration time.Duration     `json:"avg_task_duration"`
	ErrorsLastHour  int               `json:"errors_last_hour"`
	Warnings        []string          `json:"warnings,omitempty"`
	BlockageReason  string            `json:"blockage_reason,omitempty"`
	Metadata        map[string]string `json:"metadata,omitempty"`
}

// AgentStatus represents the current state of an agent
type AgentStatus int

const (
	AgentStatusUnknown AgentStatus = iota
	AgentStatusOffline
	AgentStatusIdle
	AgentStatusWorking
	AgentStatusBlocked
	AgentStatusError
	AgentStatusMaintenance
)

func (s AgentStatus) String() string {
	switch s {
	case AgentStatusOffline:
		return "offline"
	case AgentStatusIdle:
		return "idle"
	case AgentStatusWorking:
		return "working"
	case AgentStatusBlocked:
		return "blocked"
	case AgentStatusError:
		return "error"
	case AgentStatusMaintenance:
		return "maintenance"
	default:
		return "unknown"
	}
}

// Dashboard represents the agent health dashboard
type Dashboard struct {
	db       *sql.DB
	hub      *Hub
	template *template.Template
	mu       sync.RWMutex
}

// DashboardData represents the complete dashboard state
type DashboardData struct {
	Agents       []AgentHealth    `json:"agents"`
	Summary      DashboardSummary `json:"summary"`
	Timestamp    time.Time        `json:"timestamp"`
	Nodes        []NodeInfo       `json:"nodes"`
	RecentEvents []DashboardEvent `json:"recent_events"`
	Alerts       []DashboardAlert `json:"alerts"`
}

// DashboardSummary provides aggregate metrics
type DashboardSummary struct {
	TotalAgents     int     `json:"total_agents"`
	OnlineAgents    int     `json:"online_agents"`
	OfflineAgents   int     `json:"offline_agents"`
	BusyAgents      int     `json:"busy_agents"`
	IdleAgents      int     `json:"idle_agents"`
	ErrorAgents     int     `json:"error_agents"`
	TotalTasks      int     `json:"total_tasks"`
	ActiveTasks     int     `json:"active_tasks"`
	AvgContextUsage float64 `json:"avg_context_usage"`
	SystemHealth    string  `json:"system_health"`
	Uptime          string  `json:"uptime"`
	TaskThroughput  float64 `json:"task_throughput"`
	ErrorRate       float64 `json:"error_rate"`
}

// NodeInfo represents information about a node
type NodeInfo struct {
	Name         string    `json:"name"`
	Status       string    `json:"status"`
	AgentCount   int       `json:"agent_count"`
	LastSeen     time.Time `json:"last_seen"`
	Capabilities []string  `json:"capabilities"`
}

// DashboardEvent represents a recent event
type DashboardEvent struct {
	Timestamp time.Time `json:"timestamp"`
	Type      string    `json:"type"`
	AgentID   string    `json:"agent_id"`
	Message   string    `json:"message"`
	Severity  string    `json:"severity"`
}

// DashboardAlert represents an active alert
type DashboardAlert struct {
	ID           string    `json:"id"`
	Type         string    `json:"type"`
	Severity     string    `json:"severity"`
	AgentID      string    `json:"agent_id"`
	Message      string    `json:"message"`
	CreatedAt    time.Time `json:"created_at"`
	Acknowledged bool      `json:"acknowledged"`
}

// NewDashboard creates a new dashboard instance
func NewDashboard(db *sql.DB, hub *Hub) *Dashboard {
	d := &Dashboard{
		db:  db,
		hub: hub,
	}
	d.loadTemplates()
	return d
}

// loadTemplates loads HTML templates for the dashboard
func (d *Dashboard) loadTemplates() {
	tmpl := `
<!DOCTYPE html>
<html>
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title>FORGE Agent Health Dashboard</title>
	<meta http-equiv="refresh" content="5">
	<style>
		* { margin: 0; padding: 0; box-sizing: border-box; }
		body {
			font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
			background: #0d1117;
			color: #c9d1d9;
			line-height: 1.6;
		}
		.header {
			background: #161b22;
			padding: 1rem 2rem;
			border-bottom: 1px solid #30363d;
			display: flex;
			justify-content: space-between;
			align-items: center;
		}
		.header h1 {
			color: #58a6ff;
			font-size: 1.5rem;
		}
		.status-badge {
			padding: 0.25rem 0.75rem;
			border-radius: 12px;
			font-size: 0.875rem;
			font-weight: 500;
		}
		.status-healthy { background: #238636; color: white; }
		.status-warning { background: #f0883e; color: white; }
		.status-critical { background: #da3633; color: white; }
		.container {
			padding: 2rem;
			max-width: 1400px;
			margin: 0 auto;
		}
		.notice {
			margin-bottom: 1rem;
			padding: 0.875rem 1rem;
			background: #111827;
			border: 1px solid #30363d;
			border-radius: 8px;
			color: #9ca3af;
		}
		.notice a {
			color: #58a6ff;
			text-decoration: none;
		}
		.metrics-grid {
			display: grid;
			grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
			gap: 1rem;
			margin-bottom: 2rem;
		}
		.metric-card {
			background: #161b22;
			border: 1px solid #30363d;
			border-radius: 8px;
			padding: 1.5rem;
		}
		.metric-value {
			font-size: 2rem;
			font-weight: 600;
			color: #58a6ff;
		}
		.metric-label {
			font-size: 0.875rem;
			color: #8b949e;
			margin-top: 0.25rem;
		}
		.section {
			background: #161b22;
			border: 1px solid #30363d;
			border-radius: 8px;
			margin-bottom: 2rem;
			overflow: hidden;
		}
		.section-header {
			padding: 1rem 1.5rem;
			background: #21262d;
			border-bottom: 1px solid #30363d;
			font-weight: 600;
		}
		.agent-table {
			width: 100%;
			border-collapse: collapse;
		}
		.agent-table th,
		.agent-table td {
			padding: 0.75rem 1rem;
			text-align: left;
			border-bottom: 1px solid #30363d;
		}
		.agent-table th {
			background: #161b22;
			font-weight: 500;
			color: #8b949e;
			font-size: 0.75rem;
			text-transform: uppercase;
		}
		.agent-table tr:hover {
			background: #1f242c;
		}
		.agent-status {
			display: inline-flex;
			align-items: center;
			gap: 0.5rem;
			padding: 0.25rem 0.5rem;
			border-radius: 4px;
			font-size: 0.75rem;
			font-weight: 500;
		}
		.status-dot {
			width: 8px;
			height: 8px;
			border-radius: 50%;
		}
		.status-online { background: #3fb950; }
		.status-offline { background: #6e7681; }
		.status-busy { background: #f0883e; }
		.status-error { background: #da3633; }
		.context-bar {
			width: 100%;
			height: 4px;
			background: #30363d;
			border-radius: 2px;
			overflow: hidden;
		}
		.context-fill {
			height: 100%;
			border-radius: 2px;
			transition: width 0.3s ease;
		}
		.context-low { background: #3fb950; }
		.context-medium { background: #f0883e; }
		.context-high { background: #da3633; }
		.alert {
			padding: 0.75rem 1rem;
			border-left: 3px solid;
			margin: 0.5rem 1rem;
			border-radius: 0 4px 4px 0;
		}
		.alert-critical {
			background: rgba(218, 54, 51, 0.1);
			border-color: #da3633;
		}
		.alert-warning {
			background: rgba(240, 136, 62, 0.1);
			border-color: #f0883e;
		}
		.alert-info {
			background: rgba(56, 139, 253, 0.1);
			border-color: #58a6ff;
		}
		.timestamp {
			font-size: 0.75rem;
			color: #8b949e;
		}
		.event-log {
			max-height: 300px;
			overflow-y: auto;
		}
		.event-item {
			padding: 0.5rem 1rem;
			border-bottom: 1px solid #30363d;
			font-size: 0.875rem;
		}
		.event-time {
			color: #8b949e;
			font-family: monospace;
		}
		.event-type {
			display: inline-block;
			padding: 0.125rem 0.375rem;
			border-radius: 3px;
			font-size: 0.75rem;
			margin-right: 0.5rem;
		}
		.type-connected { background: #238636; }
		.type-disconnected { background: #6e7681; }
		.type-task { background: #1f6feb; }
		.type-error { background: #da3633; }
		.footer {
			text-align: center;
			padding: 2rem;
			color: #8b949e;
			font-size: 0.875rem;
		}
	</style>
</head>
<body>
	<div class="header">
		<h1>🔥 FORGE Agent Health Dashboard</h1>
		<div>
			<span class="status-badge status-{{.Summary.SystemHealth}}">{{.Summary.SystemHealth}}</span>
			<span class="timestamp">{{.Timestamp.Format "15:04:05"}}</span>
		</div>
	</div>

	<div class="container">
		<div class="notice">Preferred browser UI: <a href="/ui">/ui</a>. This page is a compatibility and debugging view.</div>
		<!-- Summary Metrics -->
		<div class="metrics-grid">
			<div class="metric-card">
				<div class="metric-value">{{.Summary.TotalAgents}}</div>
				<div class="metric-label">Total Agents</div>
			</div>
			<div class="metric-card">
				<div class="metric-value" style="color: #3fb950;">{{.Summary.OnlineAgents}}</div>
				<div class="metric-label">Online</div>
			</div>
			<div class="metric-card">
				<div class="metric-value" style="color: #f0883e;">{{.Summary.BusyAgents}}</div>
				<div class="metric-label">Busy</div>
			</div>
			<div class="metric-card">
				<div class="metric-value" style="color: #da3633;">{{.Summary.ErrorAgents}}</div>
				<div class="metric-label">Errors</div>
			</div>
			<div class="metric-card">
				<div class="metric-value">{{.Summary.ActiveTasks}}</div>
				<div class="metric-label">Active Tasks</div>
			</div>
			<div class="metric-card">
				<div class="metric-value">{{printf "%.1f" .Summary.AvgContextUsage}}%</div>
				<div class="metric-label">Avg Context</div>
			</div>
		</div>

		<!-- Alerts Section -->
		{{if .Alerts}}
		<div class="section">
			<div class="section-header">⚠️ Active Alerts</div>
			{{range .Alerts}}
			<div class="alert alert-{{.Severity}}">
				<strong>{{.Type}}</strong>: {{.Message}}
				<span class="timestamp">- {{.AgentID}} ({{.CreatedAt.Format "15:04"}})</span>
			</div>
			{{end}}
		</div>
		{{end}}

		<!-- Agents Table -->
		<div class="section">
			<div class="section-header">Agents Status</div>
			<table class="agent-table">
				<thead>
					<tr>
						<th>Agent</th>
						<th>Node</th>
						<th>Status</th>
						<th>Current Task</th>
						<th>Context Usage</th>
						<th>Success Rate</th>
						<th>Last Heartbeat</th>
					</tr>
				</thead>
				<tbody>
					{{range .Agents}}
					<tr>
						<td><strong>{{.AgentID}}</strong></td>
						<td>{{.Node}}</td>
						<td>
							<span class="agent-status">
								<span class="status-dot status-{{.StatusText}}"></span>
								{{.StatusText}}
							</span>
						</td>
						<td>{{if .CurrentTask}}{{.CurrentTask}}{{else}}-{{end}}</td>
						<td>
							<div class="context-bar">
								<div class="context-fill context-{{if lt .ContextPercent 50.0}}low{{else if lt .ContextPercent 80.0}}medium{{else}}high{{end}}" 
									 style="width: {{.ContextPercent}}%"></div>
							</div>
							<small>{{printf "%.1f" .ContextPercent}}%</small>
						</td>
						<td>{{printf "%.0f" .SuccessRate}}%</td>
						<td class="timestamp">{{.LastHeartbeat.Format "15:04:05"}}</td>
					</tr>
					{{end}}
				</tbody>
			</table>
		</div>

		<!-- Recent Events -->
		<div class="section">
			<div class="section-header">Recent Events</div>
			<div class="event-log">
				{{range .RecentEvents}}
				<div class="event-item">
					<span class="event-time">{{.Timestamp.Format "15:04:05"}}</span>
					<span class="event-type type-{{.Type}}">{{.Type}}</span>
					<strong>{{.AgentID}}</strong>: {{.Message}}
				</div>
				{{end}}
			</div>
		</div>

		<!-- Fleet Metrics (ADR-027: cross-node aggregation via /api/fleet/metrics) -->
	<div class="section">
		<div class="section-header">Fleet Metrics (ADR-027)</div>
		<div id="fleet-metrics-content" style="padding: 1rem;">
			<span style="color: #8b949e;">Loading fleet metrics...</span>
		</div>
	</div>

	<!-- Phase 2 Completion Status (real-time via /api/phase2/health) -->
		<div class="section">
			<div class="section-header">Phase 2 Completion Status</div>
			<div class="phase2-status">
				<div class="metrics-grid">
					<div class="metric-card">
						<div class="metric-value" id="phase2-percent">--%</div>
						<div class="metric-label">Complete</div>
					</div>
					<div class="metric-card">
						<div class="metric-value" id="phase2-status-text">...</div>
						<div class="metric-label">Status</div>
					</div>
				</div>
				<div id="phase2-components" style="padding: 1rem; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.5rem;">
					<!-- Components populated by JS -->
				</div>
			</div>
		</div>
	</div>

	<script>
	// ADR-027: poll /api/fleet/metrics and render per-node metric cards.
	async function updateFleetMetrics() {
		try {
			const resp = await fetch('/api/fleet/metrics');
			if (!resp.ok) return;
			const data = await resp.json();
			const el = document.getElementById('fleet-metrics-content');
			if (!el) return;
			const nodes = data.nodes || {};
			const nodeNames = Object.keys(nodes).sort();
			if (nodeNames.length === 0) {
				el.innerHTML = '<span style="color: #8b949e;">No cross-node metrics received yet.</span>';
				return;
			}
			let html = '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem;">';
			nodeNames.forEach(function(nodeName) {
				const rows = nodes[nodeName] || [];
				html += '<div style="background: #21262d; border: 1px solid #30363d; border-radius: 6px; padding: 0.75rem;">';
				html += '<div style="font-weight: 600; color: #58a6ff; margin-bottom: 0.5rem;">' + nodeName + '</div>';
				html += '<table style="width:100%; font-size: 0.8rem; border-collapse: collapse;">';
				rows.forEach(function(row) {
					html += '<tr><td style="color:#8b949e; padding:2px 0;">' + row.metric_name + '</td>';
					html += '<td style="text-align:right; color:#c9d1d9;">' + row.value.toFixed(4) + '</td>';
					html += '<td style="text-align:right; color:#6e7681; padding-left:0.5rem;">' + row.period + '</td></tr>';
				});
				html += '</table></div>';
			});
			html += '</div>';
			html += '<div style="margin-top:0.5rem; font-size:0.75rem; color:#6e7681;">Updated: ' + (data.generated_at || '') + ' | ' + data.node_count + ' node(s)</div>';
			el.innerHTML = html;
		} catch(e) {}
	}
	setInterval(updateFleetMetrics, 30000);
	updateFleetMetrics();

	async function updatePhase2Status() {
		try {
			const resp = await fetch('/api/phase2/health');
			if (!resp.ok) return;
			const health = await resp.json();
			const percentEl = document.getElementById('phase2-percent');
			const statusEl = document.getElementById('phase2-status-text');
			const componentsEl = document.getElementById('phase2-components');
			if (percentEl) percentEl.textContent = (health.completion_percent || 0) + '%';
			if (statusEl) statusEl.textContent = health.status || '--';
			if (componentsEl) {
				componentsEl.innerHTML = [
					{ key: 'context_envelopes', label: 'Context Envelopes' },
					{ key: 'bootstrap', label: 'Bootstrap' },
					{ key: 'auto_threshold', label: 'Auto Threshold' },
					{ key: 'royal_jelly', label: 'Royal Jelly' },
					{ key: 'task_integration', label: 'Task Integration' },
					{ key: 'sidecar_reading', label: 'Sidecar Reading' },
					{ key: 'e2e_automation', label: 'E2E Automation' }
				].map(function(c) {
					var ok = health[c.key];
					var status = ok ? '&#9989;' : '&#10060;';
					var color = ok ? '#3fb950' : '#da3633';
					return '<div style="padding: 0.5rem; background: #21262d; border-radius: 4px; display: flex; align-items: center; gap: 0.5rem;"><span style="color: ' + color + '">' + status + '</span><span>' + c.label + '</span></div>';
				}).join('');
			}
		} catch (e) {}
	}
	setInterval(updatePhase2Status, 30000);
	updatePhase2Status();
	</script>

	<div class="footer">
		<p>FORGE v3 Agent Health Dashboard | Uptime: {{.Summary.Uptime}}</p>
		<p>Auto-refresh: 5s | <a href="/api/dashboard" style="color: #58a6ff;">JSON API</a></p>
	</div>
</body>
</html>
`
	d.template = template.Must(template.New("dashboard").Parse(tmpl))
}

// GetAgentHealth retrieves health data for all agents
func (d *Dashboard) GetAgentHealth(ctx context.Context) ([]AgentHealth, error) {
	// Query agent heartbeats from database
	rows, err := d.db.QueryContext(ctx, `
		SELECT 
			agent_id, node, status, current_task_id, context_pct, 
			capabilities, last_seen, connected_at
		FROM agent_heartbeats
		ORDER BY last_seen DESC
	`)
	if err != nil {
		return nil, fmt.Errorf("querying agent heartbeats: %w", err)
	}
	defer rows.Close()

	var agents []AgentHealth
	now := time.Now()

	for rows.Next() {
		var a AgentHealth
		var currentTask sql.NullString
		var capabilities sql.NullString
		var lastSeen, connectedAt string

		err := rows.Scan(
			&a.AgentID, &a.Node, &a.StatusText, &currentTask,
			&a.ContextPercent, &capabilities, &lastSeen, &connectedAt,
		)
		if err != nil {
			continue
		}

		if currentTask.Valid {
			a.CurrentTask = &currentTask.String
		}
		if capabilities.Valid {
			a.Capabilities = splitCapabilities(capabilities.String)
		}

		// Parse timestamps
		a.LastHeartbeat, _ = time.Parse("2006-01-02 15:04:05", lastSeen)
		a.ConnectedAt, _ = time.Parse("2006-01-02 15:04:05", connectedAt)

		// Determine status based on heartbeat age
		a.Status = d.calculateStatus(a.LastHeartbeat, now)
		a.StatusText = a.Status.String()

		// Get task statistics
		a.TaskCount, a.SuccessRate, a.AvgTaskDuration = d.getAgentStats(ctx, a.AgentID)

		// Check for warnings
		a.Warnings = d.checkWarnings(&a)

		agents = append(agents, a)
	}

	return agents, rows.Err()
}

// calculateStatus determines agent status based on last heartbeat
func (d *Dashboard) calculateStatus(lastHeartbeat, now time.Time) AgentStatus {
	age := now.Sub(lastHeartbeat)

	if age > 5*time.Minute {
		return AgentStatusOffline
	}
	if age > 2*time.Minute {
		return AgentStatusError
	}
	return AgentStatusIdle
}

// checkWarnings generates warnings for an agent
func (d *Dashboard) checkWarnings(a *AgentHealth) []string {
	var warnings []string

	if a.ContextPercent > 80 {
		warnings = append(warnings, "High context usage")
	}
	if a.LastHeartbeat.Before(time.Now().Add(-2 * time.Minute)) {
		warnings = append(warnings, "Stale heartbeat")
	}
	if a.SuccessRate < 0.5 {
		warnings = append(warnings, "Low success rate")
	}
	if a.ErrorsLastHour > 5 {
		warnings = append(warnings, "High error rate")
	}

	return warnings
}

// getAgentStats retrieves task statistics for an agent
func (d *Dashboard) getAgentStats(ctx context.Context, agentID string) (int, float64, time.Duration) {
	// Query task statistics
	var taskCount int
	var successCount int
	var avgDuration sql.NullInt64

	// Get total tasks
	d.db.QueryRowContext(ctx, `
		SELECT COUNT(*) FROM tasks WHERE assigned_to = ?
	`, agentID).Scan(&taskCount)

	// Get successful tasks
	d.db.QueryRowContext(ctx, `
		SELECT COUNT(*) FROM tasks 
		WHERE assigned_to = ? AND status = 'completed'
	`, agentID).Scan(&successCount)

	// Get average duration
	d.db.QueryRowContext(ctx, `
		SELECT AVG(duration_ms) FROM tasks 
		WHERE assigned_to = ? AND status = 'completed' AND created_at > datetime('now', '-7 days')
	`, agentID).Scan(&avgDuration)

	var successRate float64
	if taskCount > 0 {
		successRate = float64(successCount) / float64(taskCount) * 100
	}

	var duration time.Duration
	if avgDuration.Valid {
		duration = time.Duration(avgDuration.Int64) * time.Millisecond
	}

	return taskCount, successRate, duration
}

// GetDashboardData retrieves complete dashboard data
func (d *Dashboard) GetDashboardData(ctx context.Context) (*DashboardData, error) {
	agents, err := d.GetAgentHealth(ctx)
	if err != nil {
		return nil, err
	}

	data := &DashboardData{
		Agents:       agents,
		Timestamp:    time.Now(),
		RecentEvents: d.getRecentEvents(),
		Alerts:       d.getActiveAlerts(),
	}

	// Calculate summary
	data.Summary = d.calculateSummary(agents)

	// Get node info
	data.Nodes = d.getNodeInfo(agents)

	return data, nil
}

// calculateSummary computes aggregate metrics
func (d *Dashboard) calculateSummary(agents []AgentHealth) DashboardSummary {
	summary := DashboardSummary{
		SystemHealth: "healthy",
		TotalAgents:  len(agents),
	}

	var totalContext float64
	errorCount := 0

	for _, a := range agents {
		totalContext += a.ContextPercent

		switch a.Status {
		case AgentStatusOffline:
			summary.OfflineAgents++
		case AgentStatusIdle:
			summary.IdleAgents++
			summary.OnlineAgents++
		case AgentStatusWorking, AgentStatusBlocked:
			summary.BusyAgents++
			summary.OnlineAgents++
		case AgentStatusError:
			summary.ErrorAgents++
			summary.OnlineAgents++
			errorCount++
		}

		if a.CurrentTask != nil {
			summary.ActiveTasks++
		}
	}

	if summary.TotalAgents > 0 {
		summary.AvgContextUsage = totalContext / float64(summary.TotalAgents)
	}

	// Determine system health
	if errorCount > 0 {
		summary.SystemHealth = "warning"
	}
	if summary.OfflineAgents > summary.OnlineAgents {
		summary.SystemHealth = "critical"
	}

	// Get uptime
	summary.Uptime = d.getUptime()

	// Calculate throughput and error rate from database
	summary.TaskThroughput, summary.ErrorRate = d.getGlobalStats(context.Background())

	return summary
}

// getGlobalStats calculates system-wide metrics
func (d *Dashboard) getGlobalStats(ctx context.Context) (float64, float64) {
	var throughput float64
	var errorRate float64

	// Tasks in last hour
	var lastHourCount int
	d.db.QueryRowContext(ctx, `
		SELECT COUNT(*) FROM tasks 
		WHERE created_at > datetime('now', '-1 hour')
	`).Scan(&lastHourCount)
	throughput = float64(lastHourCount)

	// Total tasks and failed tasks for error rate
	var totalTasks, failedTasks int
	d.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM tasks WHERE created_at > datetime('now', '-24 hours')`).Scan(&totalTasks)
	d.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM tasks WHERE status = 'failed' AND created_at > datetime('now', '-24 hours')`).Scan(&failedTasks)

	if totalTasks > 0 {
		errorRate = (float64(failedTasks) / float64(totalTasks)) * 100
	}

	return throughput, errorRate
}

// getNodeInfo extracts node information from agents
func (d *Dashboard) getNodeInfo(agents []AgentHealth) []NodeInfo {
	nodeMap := make(map[string]*NodeInfo)

	for _, a := range agents {
		if _, exists := nodeMap[a.Node]; !exists {
			nodeMap[a.Node] = &NodeInfo{
				Name:     a.Node,
				Status:   "online",
				LastSeen: a.LastHeartbeat,
			}
		}
		nodeMap[a.Node].AgentCount++
	}

	nodes := make([]NodeInfo, 0, len(nodeMap))
	for _, n := range nodeMap {
		nodes = append(nodes, *n)
	}

	sort.Slice(nodes, func(i, j int) bool {
		return nodes[i].Name < nodes[j].Name
	})

	return nodes
}

// getRecentEvents retrieves recent dashboard events
func (d *Dashboard) getRecentEvents() []DashboardEvent {
	// This would typically query an events table
	// For now, return mock events based on hub state
	var events []DashboardEvent

	if d.hub != nil {
		workers := d.hub.ListWorkers()
		for _, worker := range workers {
			events = append(events, DashboardEvent{
				Timestamp: time.Now(),
				Type:      "connected",
				AgentID:   worker,
				Message:   "Agent connected",
				Severity:  "info",
			})
		}
	}

	return events
}

// getActiveAlerts retrieves active alerts
func (d *Dashboard) getActiveAlerts() []DashboardAlert {
	// This would query an alerts table
	// Return empty for now
	return []DashboardAlert{}
}

// getUptime returns server uptime
func (d *Dashboard) getUptime() string {
	// This would track actual server start time
	return "2h 15m"
}

// splitCapabilities splits a comma-separated capability string
func splitCapabilities(s string) []string {
	if s == "" {
		return nil
	}
	parts := splitString(s, ",")
	for i := range parts {
		parts[i] = trimSpace(parts[i])
	}
	return parts
}

// splitString splits a string by separator
func splitString(s, sep string) []string {
	var result []string
	start := 0
	for i := 0; i+len(sep) <= len(s); i++ {
		if s[i:i+len(sep)] == sep {
			result = append(result, s[start:i])
			start = i + len(sep)
		}
	}
	result = append(result, s[start:])
	return result
}

// trimSpace removes leading/trailing whitespace
func trimSpace(s string) string {
	start := 0
	for start < len(s) && (s[start] == ' ' || s[start] == '\t' || s[start] == '\n' || s[start] == '\r') {
		start++
	}
	end := len(s)
	for end > start && (s[end-1] == ' ' || s[end-1] == '\t' || s[end-1] == '\n' || s[end-1] == '\r') {
		end--
	}
	return s[start:end]
}

// HTTP Handlers

// ServeDashboard serves the HTML dashboard
func (d *Dashboard) ServeDashboard(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	data, err := d.GetDashboardData(ctx)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/html")
	d.template.Execute(w, data)
}

// ServeDashboardJSON serves dashboard data as JSON
func (d *Dashboard) ServeDashboardJSON(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	data, err := d.GetDashboardData(ctx)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(data)
}

// ServeAgentHealth serves health data for a specific agent
func (d *Dashboard) ServeAgentHealth(w http.ResponseWriter, r *http.Request) {
	agentID := r.URL.Path[len("/api/dashboard/agents/"):]
	if agentID == "" {
		http.Error(w, "agent ID required", http.StatusBadRequest)
		return
	}

	ctx := r.Context()
	agents, err := d.GetAgentHealth(ctx)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	for _, a := range agents {
		if a.AgentID == agentID {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(a)
			return
		}
	}

	http.Error(w, "agent not found", http.StatusNotFound)
}

// ServeAgentsDashboard serves agent list for /api/agents/dashboard endpoint
func (d *Dashboard) ServeAgentsDashboard(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	ctx := r.Context()
	agents, err := d.GetAgentHealth(ctx)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"agents":    agents,
		"count":     len(agents),
		"timestamp": time.Now(),
	})
}

// RegisterRoutes registers dashboard HTTP routes
func (d *Dashboard) RegisterRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/dashboard", d.ServeDashboard)
	mux.HandleFunc("/api/dashboard", d.ServeDashboardJSON)
	mux.HandleFunc("/api/dashboard/agents/", d.ServeAgentHealth)
	mux.HandleFunc("/api/agents/dashboard", d.ServeAgentsDashboard)
}
