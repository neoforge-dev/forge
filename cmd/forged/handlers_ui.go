//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"fmt"
	"net/http"
	"time"
)

// uiFleetHandler serves the HTMX fleet dashboard at GET /ui
// Shows: agent health, task queue, patrol status, scaling recs.
// ADR-019/027: Fleet Observability + Terminal Control Surface.
func uiFleetHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	now := time.Now().UTC()

	// --- Agents ---
	type agentRow struct {
		AgentID    string
		Node       string
		Status     string
		ContextPct float64
		TaskID     string
		LastSeen   string
	}
	var agents []agentRow
	if db != nil {
		rows, err := db.QueryContext(r.Context(), `
			SELECT agent_id, node, status, context_pct, COALESCE(current_task_id,''), last_seen
			FROM agent_heartbeats
			ORDER BY node, agent_id
			LIMIT 50
		`)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var a agentRow
				if err := rows.Scan(&a.AgentID, &a.Node, &a.Status, &a.ContextPct, &a.TaskID, &a.LastSeen); err == nil {
					agents = append(agents, a)
				}
			}
		}
	}

	// --- Queue counts ---
	var queued, running, completed, failed int
	if db != nil {
		db.QueryRow(`SELECT COUNT(*) FROM tasks WHERE status='queued'`).Scan(&queued)
		db.QueryRow(`SELECT COUNT(*) FROM tasks WHERE status IN ('assigned','executing')`).Scan(&running)
		db.QueryRow(`SELECT COUNT(*) FROM tasks WHERE status='completed' AND updated_at > datetime('now','-24 hours')`).Scan(&completed)
		db.QueryRow(`SELECT COUNT(*) FROM tasks WHERE status IN ('failed','abandoned') AND updated_at > datetime('now','-24 hours')`).Scan(&failed)
	}

	// --- Recent tasks ---
	type taskRow struct {
		ID         string
		Domain     string
		Status     string
		AssignedTo string
		Priority   int
		UpdatedAt  string
	}
	var recentTasks []taskRow
	if db != nil {
		rows, err := db.QueryContext(r.Context(), `
			SELECT id, COALESCE(domain,''), status, COALESCE(assigned_to,''), priority, COALESCE(updated_at,'')
			FROM tasks
			ORDER BY updated_at DESC
			LIMIT 15
		`)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var t taskRow
				if err := rows.Scan(&t.ID, &t.Domain, &t.Status, &t.AssignedTo, &t.Priority, &t.UpdatedAt); err == nil {
					recentTasks = append(recentTasks, t)
				}
			}
		}
	}

	// --- Patrol summary ---
	type patrolSummary struct {
		PatrolID string
		Status   string
		LastRun  string
		Errors   int
	}
	var patrols []patrolSummary
	if db != nil {
		rows, err := db.QueryContext(r.Context(), `
			SELECT patrol_id,
			       MAX(CASE WHEN status='running' THEN 'running' ELSE status END) AS status,
			       MAX(started_at) AS last_run,
			       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors
			FROM patrol_executions
			GROUP BY patrol_id
			ORDER BY last_run DESC
			LIMIT 20
		`)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var p patrolSummary
				var lastRun sql.NullString
				if err := rows.Scan(&p.PatrolID, &p.Status, &lastRun, &p.Errors); err == nil {
					if lastRun.Valid {
						p.LastRun = lastRun.String
						if len(p.LastRun) > 16 {
							p.LastRun = p.LastRun[:16]
						}
					}
					patrols = append(patrols, p)
				}
			}
		}
	}

	// Count active agents (seen in last 5 min)
	activeAgents := 0
	for _, a := range agents {
		if t, err := time.Parse("2006-01-02 15:04:05", a.LastSeen); err == nil {
			if now.Sub(t) < 5*time.Minute {
				activeAgents++
			}
		}
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")

	fmt.Fprintf(w, `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FORGE Fleet Dashboard</title>
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#c9d1d9;padding:16px}
    header{display:flex;align-items:center;gap:16px;margin-bottom:16px}
    h1{color:#58a6ff;font-size:18px}
    nav a{color:#58a6ff;text-decoration:none;font-size:13px;margin-right:12px;opacity:.7}
    nav a:hover{opacity:1}
    .ts{font-size:11px;color:#8b949e;margin-left:auto}
    .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
    .card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:14px}
    .card h2{font-size:11px;text-transform:uppercase;color:#8b949e;margin-bottom:8px;letter-spacing:.05em}
    .big{font-size:28px;font-weight:700}
    .green{color:#3fb950}.yellow{color:#d29922}.red{color:#f85149}.blue{color:#58a6ff}.grey{color:#8b949e}
    .section{background:#161b22;border:1px solid #30363d;border-radius:6px;margin-bottom:12px;overflow:hidden}
    .section-hdr{background:#21262d;padding:8px 12px;font-size:12px;font-weight:600;text-transform:uppercase;color:#8b949e;letter-spacing:.05em}
    table{width:100%%;border-collapse:collapse}
    td,th{padding:7px 12px;font-size:13px;text-align:left;border-bottom:1px solid #21262d;white-space:nowrap}
    th{color:#8b949e;font-weight:600;font-size:11px;text-transform:uppercase}
    tr:last-child td{border-bottom:none}
    .pill{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600}
    .pill-online{background:#1a3a24;color:#3fb950}
    .pill-offline{background:#3a1a1a;color:#f85149}
    .pill-queued{background:#1a2a3a;color:#58a6ff}
    .pill-assigned,.pill-executing{background:#3a2a1a;color:#d29922}
    .pill-completed{background:#1a3a24;color:#3fb950}
    .pill-abandoned,.pill-failed{background:#3a1a1a;color:#f85149}
    .ctx-bar{display:inline-block;height:8px;border-radius:4px;background:#3fb950;vertical-align:middle;margin-right:4px}
    .two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  </style>
</head>
<body hx-get="/ui" hx-trigger="every 20s" hx-swap="outerHTML">
<header>
  <h1>FORGE Fleet</h1>
  <nav>
    <a href="/ui">Fleet</a>
    <a href="/dash">Patrols</a>
  </nav>
  <span class="ts">Updated: %s</span>
</header>

<div class="grid">
  <div class="card"><h2>Active Agents</h2><div class="big blue">%d</div></div>
  <div class="card"><h2>Queued Tasks</h2><div class="big yellow">%d</div></div>
  <div class="card"><h2>Completed (24h)</h2><div class="big green">%d</div></div>
  <div class="card"><h2>Failed (24h)</h2><div class="big red">%d</div></div>
</div>

<div class="two-col">
`, now.Format("15:04:05 UTC"), activeAgents, queued, completed, failed)

	// Agent table
	fmt.Fprint(w, `<div class="section">
  <div class="section-hdr">Agents</div>
  <table>
    <thead><tr><th>Agent</th><th>Node</th><th>Ctx</th><th>Task</th><th>Status</th></tr></thead>
    <tbody>
`)
	if len(agents) == 0 {
		fmt.Fprint(w, `<tr><td colspan="5" class="grey" style="text-align:center;padding:20px">No agents registered</td></tr>`)
	}
	for _, a := range agents {
		pillClass := "pill-offline"
		if a.Status == "online" || a.Status == "connected" {
			pillClass = "pill-online"
		}
		ctxPct := int(a.ContextPct)
		if ctxPct > 100 {
			ctxPct = 100
		}
		ctxColor := "#3fb950"
		if ctxPct > 75 {
			ctxColor = "#f85149"
		} else if ctxPct > 50 {
			ctxColor = "#d29922"
		}
		taskShort := a.TaskID
		if len(taskShort) > 20 {
			taskShort = taskShort[:20] + "…"
		}
		agentShort := a.AgentID
		if len(agentShort) > 18 {
			agentShort = agentShort[:18]
		}
		fmt.Fprintf(w, `<tr>
      <td>%s</td>
      <td class="grey">%s</td>
      <td><span class="ctx-bar" style="width:%dpx;background:%s"></span>%d%%</td>
      <td class="grey" style="font-size:11px">%s</td>
      <td><span class="pill %s">%s</span></td>
    </tr>`, agentShort, a.Node, ctxPct/2, ctxColor, ctxPct, taskShort, pillClass, a.Status)
	}
	fmt.Fprint(w, `</tbody></table></div>`)

	// Patrol summary
	fmt.Fprint(w, `<div class="section">
  <div class="section-hdr">Patrols</div>
  <table>
    <thead><tr><th>Patrol</th><th>Last Run</th><th>Errors</th></tr></thead>
    <tbody>
`)
	if len(patrols) == 0 {
		fmt.Fprint(w, `<tr><td colspan="3" class="grey" style="text-align:center;padding:20px">No patrols run yet</td></tr>`)
	}
	for _, p := range patrols {
		errColor := "grey"
		if p.Errors > 0 {
			errColor = "red"
		}
		pShort := p.PatrolID
		if len(pShort) > 24 {
			pShort = pShort[:24] + "…"
		}
		fmt.Fprintf(w, `<tr>
      <td>%s</td>
      <td class="grey" style="font-size:11px">%s</td>
      <td class="%s">%d</td>
    </tr>`, pShort, p.LastRun, errColor, p.Errors)
	}
	fmt.Fprint(w, `</tbody></table></div>`)

	fmt.Fprint(w, `</div>`) // end two-col

	// Recent tasks
	fmt.Fprint(w, `<div class="section">
  <div class="section-hdr">Recent Tasks</div>
  <table>
    <thead><tr><th>ID</th><th>Domain</th><th>Status</th><th>Agent</th><th>Pri</th><th>Updated</th></tr></thead>
    <tbody>
`)
	if len(recentTasks) == 0 {
		fmt.Fprint(w, `<tr><td colspan="6" class="grey" style="text-align:center;padding:20px">No tasks</td></tr>`)
	}
	for _, t := range recentTasks {
		pillClass := "pill-" + t.Status
		idShort := t.ID
		if len(idShort) > 24 {
			idShort = idShort[:24] + "…"
		}
		updatedShort := t.UpdatedAt
		if len(updatedShort) > 16 {
			updatedShort = updatedShort[:16]
		}
		agentShort := t.AssignedTo
		if len(agentShort) > 12 {
			agentShort = agentShort[:12]
		}
		fmt.Fprintf(w, `<tr>
      <td style="font-family:monospace;font-size:12px">%s</td>
      <td class="grey">%s</td>
      <td><span class="pill %s">%s</span></td>
      <td class="grey">%s</td>
      <td class="grey" style="text-align:center">%d</td>
      <td class="grey" style="font-size:11px">%s</td>
    </tr>`, idShort, t.Domain, pillClass, t.Status, agentShort, t.Priority, updatedShort)
	}
	fmt.Fprint(w, `</tbody></table></div>`)

	fmt.Fprint(w, `</body></html>`)
}
