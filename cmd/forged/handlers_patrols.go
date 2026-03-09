//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// patrolEntry is the public shape returned by GET /api/patrols.
type patrolEntry struct {
	ID              string  `json:"id"`
	Name            string  `json:"name"`
	Status          string  `json:"status"`
	LastRun         *string `json:"last_run"`
	IntervalSeconds float64 `json:"interval_seconds"`
	ErrorCount      int     `json:"error_count"`
}

func patrolsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Build a baseline list from the configured patrol system.  If the system
	// hasn't been initialised yet (e.g. tests that don't start the daemon) we
	// fall through to the DB-only path.
	type configuredPatrol struct {
		name     string
		interval time.Duration
	}
	configured := make(map[string]configuredPatrol)
	var orderedIDs []string

	if globalPatrolSystem != nil {
		allPatrols := globalPatrolSystem.ListPatrols()
		for _, p := range allPatrols {
			configured[p.ID] = configuredPatrol{name: p.Name, interval: p.Schedule}
			orderedIDs = append(orderedIDs, p.ID)
		}
		for _, cp := range globalPatrolSystem.contextPatrols {
			if _, exists := configured[cp.ID]; !exists {
				configured[cp.ID] = configuredPatrol{name: cp.Name, interval: cp.Schedule}
				orderedIDs = append(orderedIDs, cp.ID)
			}
		}
	}

	// Seed result map from configured patrols; status defaults to "stopped"
	// until execution records tell us otherwise.
	result := make(map[string]*patrolEntry, len(configured))
	for id, cfg := range configured {
		entry := &patrolEntry{
			ID:              id,
			Name:            cfg.name,
			Status:          "stopped",
			IntervalSeconds: cfg.interval.Seconds(),
		}
		result[id] = entry
	}

	// Enrich with live execution data from the DB (if available).
	db := getDBConn()
	if db != nil {
		rows, err := db.Query(`
			SELECT patrol_id,
			       MAX(started_at)                                          AS last_run,
			       MAX(CASE WHEN status = 'running' THEN 1 ELSE 0 END)     AS is_running,
			       SUM(CASE WHEN status = 'error'   THEN 1 ELSE 0 END)     AS error_count
			FROM patrol_executions
			GROUP BY patrol_id
			ORDER BY patrol_id
		`)
		if err != nil {
			http.Error(w, fmt.Sprintf("failed to query patrols: %v", err), http.StatusInternalServerError)
			return
		}
		defer rows.Close()

		for rows.Next() {
			var patrolID string
			var lastRun sql.NullString
			var isRunning, errCount int
			if err := rows.Scan(&patrolID, &lastRun, &isRunning, &errCount); err != nil {
				continue
			}

			e, found := result[patrolID]
			if !found {
				// Patrol ran but is no longer configured — surface it anyway.
				e = &patrolEntry{
					ID:     patrolID,
					Name:   patrolID,
					Status: "stopped",
				}
				result[patrolID] = e
				orderedIDs = append(orderedIDs, patrolID)
			}

			if lastRun.Valid && lastRun.String != "" {
				s := lastRun.String
				// Normalise SQLite timestamp to RFC3339.
				if t, err := time.Parse("2006-01-02 15:04:05", s); err == nil {
					s = t.UTC().Format(time.RFC3339)
				}
				e.LastRun = &s
			}

			e.ErrorCount = errCount
			if isRunning == 1 {
				e.Status = "running"
			} else if errCount > 0 {
				e.Status = "warning"
			} else {
				e.Status = "running" // has executed at least once and last run was clean
			}
		}
	}

	// Build ordered output slice.
	patrols := make([]patrolEntry, 0, len(result))
	seen := make(map[string]bool)
	for _, id := range orderedIDs {
		if !seen[id] {
			seen[id] = true
			if e, ok := result[id]; ok {
				patrols = append(patrols, *e)
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"patrols": patrols,
		"count":   len(patrols),
	})
}

// fleetSnapshotHandler returns a live fleet view (ADR-014 CC retirement gate).
// nodesHealthHandler returns per-node health aggregated from agent_heartbeats.
// GET /api/nodes/health — ADR-014 CC retirement gate.
func nodesHealthHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "database unavailable", http.StatusServiceUnavailable)
		return
	}

	rows, err := db.QueryContext(r.Context(), `
		SELECT
			node,
			COUNT(*) AS total,
			SUM(CASE WHEN status = 'online' THEN 1 ELSE 0 END) AS online_count,
			MAX(last_seen) AS last_seen
		FROM agent_heartbeats
		GROUP BY node
		ORDER BY node
	`)
	if err != nil {
		http.Error(w, "query failed", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type NodeHealth struct {
		NodeID       string `json:"node_id"`
		AgentCount   int    `json:"agent_count"`
		OnlineCount  int    `json:"online_count"`
		OfflineCount int    `json:"offline_count"`
		LastSeen     string `json:"last_seen"`
		Status       string `json:"status"`
	}

	var nodes []NodeHealth
	for rows.Next() {
		var n NodeHealth
		var lastSeen sql.NullString
		if err := rows.Scan(&n.NodeID, &n.AgentCount, &n.OnlineCount, &lastSeen); err != nil {
			continue
		}
		n.OfflineCount = n.AgentCount - n.OnlineCount
		if lastSeen.Valid {
			n.LastSeen = lastSeen.String
		}
		if n.OnlineCount > 0 {
			n.Status = "healthy"
		} else if n.AgentCount > 0 {
			n.Status = "degraded"
		} else {
			n.Status = "unknown"
		}
		nodes = append(nodes, n)
	}

	if nodes == nil {
		nodes = []NodeHealth{}
	}

	healthyCount := 0
	for _, n := range nodes {
		if n.Status == "healthy" {
			healthyCount++
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"nodes":         nodes,
		"total_nodes":   len(nodes),
		"healthy_nodes": healthyCount,
	})
}

func fleetSnapshotHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	db := getDBConn()
	if db == nil {
		http.Error(w, "database not initialized", http.StatusServiceUnavailable)
		return
	}

	// Query all agents from agent_heartbeats
	rows, err := db.Query(`
		SELECT agent_id, node, status, current_task_id, context_pct, last_seen
		FROM agent_heartbeats
		ORDER BY node, agent_id
	`)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to query agents: %v", err), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	// Group agents by node
	type agentEntry struct {
		AgentID    string  `json:"agent_id"`
		Status     string  `json:"status"`
		ContextPct float64 `json:"context_pct"`
		TaskID     string  `json:"task_id,omitempty"`
		LastSeen   string  `json:"last_seen"`
	}
	nodeAgents := make(map[string][]agentEntry)
	var totalAgents int

	for rows.Next() {
		var agentID, node, status, lastSeen string
		var currentTask sql.NullString
		var contextPct float64
		if err := rows.Scan(&agentID, &node, &status, &currentTask, &contextPct, &lastSeen); err != nil {
			continue
		}
		// Normalize last_seen to RFC3339
		if t, err := time.Parse("2006-01-02 15:04:05", lastSeen); err == nil {
			lastSeen = t.UTC().Format(time.RFC3339)
		}
		taskID := ""
		if currentTask.Valid {
			taskID = currentTask.String
		}
		nodeAgents[node] = append(nodeAgents[node], agentEntry{
			AgentID:    agentID,
			Status:     status,
			ContextPct: contextPct,
			TaskID:     taskID,
			LastSeen:   lastSeen,
		})
		totalAgents++
	}
	if err := rows.Err(); err != nil {
		http.Error(w, fmt.Sprintf("failed to read agents: %v", err), http.StatusInternalServerError)
		return
	}

	// Count queued tasks
	var totalQueued int
	if err := db.QueryRow(`SELECT COUNT(*) FROM tasks WHERE status = 'queued'`).Scan(&totalQueued); err != nil {
		totalQueued = 0
	}

	// Build nodes array
	type nodeEntry struct {
		NodeID     string       `json:"node_id"`
		AgentCount int          `json:"agent_count"`
		QueueDepth int          `json:"queue_depth"`
		Agents     []agentEntry `json:"agents"`
	}
	nodes := make([]nodeEntry, 0, len(nodeAgents))
	for node, agents := range nodeAgents {
		nodes = append(nodes, nodeEntry{
			NodeID:     node,
			AgentCount: len(agents),
			QueueDepth: totalQueued, // shared queue
			Agents:     agents,
		})
	}

	resp := map[string]interface{}{
		"snapshot_at":  time.Now().UTC().Format(time.RFC3339),
		"nodes":        nodes,
		"total_agents": totalAgents,
		"total_queued": totalQueued,
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// fleetRecommendationsHandler returns pending scale recommendations.
// GET /api/fleet/recommendations — ADR-034 Phase 1.
func fleetRecommendationsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	db := getDBConn()
	if db == nil {
		http.Error(w, "database not initialized", http.StatusServiceUnavailable)
		return
	}

	rows, err := db.QueryContext(r.Context(), `
		SELECT id, node_id, action, agent_type, reason,
		       queue_depth, idle_agents, auto_execute, status, created_at, expires_at
		FROM scale_recommendations
		WHERE status = 'pending'
		ORDER BY created_at DESC
	`)
	if err != nil {
		// Table may not exist yet if the patrol has never run.
		http.Error(w, fmt.Sprintf("query failed: %v", err), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	var recs []ScaleRecommendation
	for rows.Next() {
		var rec ScaleRecommendation
		if err := rows.Scan(
			&rec.ID, &rec.NodeID, &rec.Action, &rec.AgentType, &rec.Reason,
			&rec.QueueDepth, &rec.IdleAgents, &rec.AutoExecute, &rec.Status,
			&rec.CreatedAt, &rec.ExpiresAt,
		); err != nil {
			continue
		}
		recs = append(recs, rec)
	}
	if recs == nil {
		recs = []ScaleRecommendation{}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"recommendations": recs,
		"count":           len(recs),
	})
}

type InboxListResponse struct {
	Nodes []InboxNode `json:"nodes"`
}

type InboxNode struct {
	Name   string `json:"name"`
	Path   string `json:"path"`
	Exists bool   `json:"exists"`
	Count  int    `json:"count"`
}

// patrolExecution represents a patrol execution record from the database
type patrolExecution struct {
	ID          string  `json:"id"`
	PatrolID    string  `json:"patrol_id"`
	StartedAt   string  `json:"started_at"`
	CompletedAt *string `json:"completed_at,omitempty"`
	Status      string  `json:"status"`
	Result      *string `json:"result,omitempty"`
	Error       *string `json:"error,omitempty"`
	DurationMs  *int64  `json:"duration_ms,omitempty"`
}

// patrolExecutionsHandler returns recent patrol executions (GET /api/patrol-executions)
// Query params:
//
//	?hours=24       — filter to last N hours (default: 24)
//	?patrol_id=X    — filter by patrol name
//	?status=error   — filter by status (ok/error/completed/running)
//	?limit=50       — max rows (default: 50, max: 200)
func patrolExecutionsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "database unavailable", http.StatusServiceUnavailable)
		return
	}

	q := r.URL.Query()

	// ?limit=N (default 50, max 200)
	limit := 50
	if l := q.Get("limit"); l != "" {
		if parsed, err := fmt.Sscanf(l, "%d", &limit); err == nil && parsed == 1 && limit > 0 && limit <= 200 {
			// valid
		} else {
			limit = 50
		}
	}

	// ?hours=N (default 24)
	hours := 24
	if h := q.Get("hours"); h != "" {
		if parsed, err := fmt.Sscanf(h, "%d", &hours); err == nil && parsed == 1 && hours > 0 {
			// valid
		} else {
			hours = 24
		}
	}

	// ?patrol_id=X and ?status=X (empty = no filter)
	patrolID := q.Get("patrol_id")
	status := q.Get("status")

	rows, err := db.QueryContext(r.Context(), `
		SELECT id, patrol_id, started_at, completed_at, status, result, error
		FROM patrol_executions
		WHERE started_at > datetime('now', '-' || ? || ' hours')
		  AND (? = '' OR patrol_id = ?)
		  AND (? = '' OR status = ?)
		ORDER BY started_at DESC
		LIMIT ?
	`, hours, patrolID, patrolID, status, status, limit)

	if err != nil {
		http.Error(w, fmt.Sprintf("query failed: %v", err), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	executions := []patrolExecution{}
	for rows.Next() {
		var e patrolExecution
		var completedAt, result, errMsg sql.NullString
		if err := rows.Scan(&e.ID, &e.PatrolID, &e.StartedAt, &completedAt, &e.Status, &result, &errMsg); err != nil {
			continue
		}
		if completedAt.Valid {
			e.CompletedAt = &completedAt.String
			// Calculate duration in ms
			if start, err1 := time.Parse("2006-01-02 15:04:05", e.StartedAt); err1 == nil {
				if end, err2 := time.Parse("2006-01-02 15:04:05", completedAt.String); err2 == nil {
					dur := end.Sub(start).Milliseconds()
					e.DurationMs = &dur
				}
			}
		}
		if result.Valid {
			e.Result = &result.String
		}
		if errMsg.Valid {
			e.Error = &errMsg.String
		}
		executions = append(executions, e)
	}

	resp := map[string]interface{}{
		"executions": executions,
		"count":      len(executions),
		"limit":      limit,
		"hours":      hours,
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// dashHandler returns HTMX dashboard HTML for patrol executions (GET /dash)
func dashHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "database unavailable", http.StatusServiceUnavailable)
		return
	}

	// Get recent executions
	rows, err := db.QueryContext(r.Context(), `
		SELECT id, patrol_id, started_at, completed_at, status, result, error
		FROM patrol_executions
		ORDER BY started_at DESC
		LIMIT 50
	`)
	if err != nil {
		http.Error(w, fmt.Sprintf("query failed: %v", err), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type execRow struct {
		ID, PatrolID, StartedAt, Status string
		CompletedAt, Result, Error      *string
		DurationMs                      *int64
	}
	executions := []execRow{}
	for rows.Next() {
		var e execRow
		var completedAt, result, errMsg sql.NullString
		if err := rows.Scan(&e.ID, &e.PatrolID, &e.StartedAt, &completedAt, &e.Status, &result, &errMsg); err != nil {
			continue
		}
		if completedAt.Valid {
			e.CompletedAt = &completedAt.String
			// Calculate duration in ms
			if start, err1 := time.Parse("2006-01-02 15:04:05", e.StartedAt); err1 == nil {
				if end, err2 := time.Parse("2006-01-02 15:04:05", completedAt.String); err2 == nil {
					dur := end.Sub(start).Milliseconds()
					e.DurationMs = &dur
				}
			}
		}
		if result.Valid {
			e.Result = &result.String
		}
		if errMsg.Valid {
			e.Error = &errMsg.String
		}
		executions = append(executions, e)
	}

	// Build HTMX HTML
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	fmt.Fprint(w, `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Forge v3 Dashboard</title>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <style>
        body { font-family: system-ui, -apple-system, sans-serif; margin: 20px; background: #0d1117; color: #c9d1d9; }
        h1 { color: #58a6ff; margin-bottom: 10px; }
        .stats { display: flex; gap: 20px; margin-bottom: 20px; }
        .stat { background: #161b22; padding: 15px 20px; border-radius: 6px; border: 1px solid #30363d; }
        .stat-value { font-size: 24px; font-weight: bold; color: #7ee787; }
        .stat-label { color: #8b949e; font-size: 12px; text-transform: uppercase; }
        table { width: 100%; border-collapse: collapse; background: #161b22; border-radius: 6px; overflow: hidden; }
        th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #30363d; }
        th { background: #21262d; color: #8b949e; font-weight: 600; font-size: 12px; text-transform: uppercase; }
        .status-completed { color: #7ee787; }
        .status-running { color: #d29922; }
        .status-error { color: #f85149; }
        .auto-refresh { font-size: 12px; color: #8b949e; margin-bottom: 10px; }
    </style>
</head>
<body>
    <h1>Forge v3 Patrol Dashboard</h1>
    <div class="auto-refresh">Auto-refresh: <span id="last-update">`+time.Now().Format(time.RFC3339)+`</span></div>
    <div id="content" hx-get="/dash/content" hx-trigger="every 10s">
`)

	// Stats
	var totalRuns, completedRuns, errorRuns int
	db.QueryRow(`SELECT COUNT(*) FROM patrol_executions`).Scan(&totalRuns)
	db.QueryRow(`SELECT COUNT(*) FROM patrol_executions WHERE status = 'completed'`).Scan(&completedRuns)
	db.QueryRow(`SELECT COUNT(*) FROM patrol_executions WHERE status = 'error'`).Scan(&errorRuns)

	fmt.Fprintf(w, `        <div class="stats">
            <div class="stat"><div class="stat-value">%d</div><div class="stat-label">Total Runs</div></div>
            <div class="stat"><div class="stat-value">%d</div><div class="stat-label">Completed</div></div>
            <div class="stat"><div class="stat-value">%d</div><div class="stat-label">Errors</div></div>
        </div>
`, totalRuns, completedRuns, errorRuns)

	fmt.Fprint(w, `        <table>
            <thead><tr><th>Patrol</th><th>Started</th><th>Duration</th><th>Status</th><th>Error</th></tr></thead>
            <tbody>
`)
	for _, e := range executions {
		statusClass := "status-" + e.Status
		started := e.StartedAt
		if len(started) > 19 {
			started = started[:19]
		}
		durationStr := "-"
		if e.DurationMs != nil {
			if *e.DurationMs < 1000 {
				durationStr = fmt.Sprintf("%dms", *e.DurationMs)
			} else {
				durationStr = fmt.Sprintf("%.1fs", float64(*e.DurationMs)/1000)
			}
		}
		errShort := ""
		if e.Error != nil && *e.Error != "" {
			if len(*e.Error) > 50 {
				errShort = (*e.Error)[:50] + "..."
			} else {
				errShort = *e.Error
			}
		}
		fmt.Fprintf(w, `                <tr>
                    <td>%s</td>
                    <td>%s</td>
                    <td>%s</td>
                    <td class="%s">%s</td>
                    <td style="color:#f85149;font-size:12px;">%s</td>
                </tr>
`, e.PatrolID, started, durationStr, statusClass, e.Status, errShort)
	}

	fmt.Fprint(w, `            </tbody>
        </table>
`)

	// Pending Approvals section
	approvalRows, approvalErr := db.QueryContext(r.Context(), `
		SELECT id, title, agent_id, domain, type, risk_score, confidence_score, created_at
		FROM approvals
		WHERE status = 'pending'
		ORDER BY created_at DESC
		LIMIT 20
	`)
	if approvalErr == nil {
		defer approvalRows.Close()

		type approvalRow struct {
			ID, Title, AgentID, Domain, Type string
			RiskScore, ConfidenceScore       float64
			CreatedAt                        string
		}
		var pendingApprovals []approvalRow
		for approvalRows.Next() {
			var a approvalRow
			if err := approvalRows.Scan(&a.ID, &a.Title, &a.AgentID, &a.Domain, &a.Type, &a.RiskScore, &a.ConfidenceScore, &a.CreatedAt); err != nil {
				continue
			}
			pendingApprovals = append(pendingApprovals, a)
		}

		fmt.Fprint(w, `
    <h2 style="color:#f0883e;margin:20px 0 10px;">Pending Approvals</h2>
    <style>
        .approvals-list { list-style: none; padding: 0; margin: 0; }
        .approval-item { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 12px 16px; margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
        .approval-meta { flex: 1; min-width: 0; }
        .approval-title { font-weight: 600; color: #c9d1d9; margin-bottom: 4px; }
        .approval-detail { font-size: 12px; color: #8b949e; }
        .approval-scores { display: flex; gap: 12px; align-items: center; font-size: 13px; }
        .confidence { color: #7ee787; }
        .risk { color: #f85149; }
        .approval-actions { display: flex; gap: 8px; flex-shrink: 0; }
        .btn-approve { background: #238636; color: #fff; border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: 600; }
        .btn-approve:hover { background: #2ea043; }
        .btn-reject { background: #da3633; color: #fff; border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: 600; }
        .btn-reject:hover { background: #f85149; }
        .no-approvals { color: #8b949e; font-size: 14px; padding: 10px 0; }
    </style>
`)

		if len(pendingApprovals) == 0 {
			fmt.Fprint(w, `    <p class="no-approvals">No pending approvals.</p>
`)
		} else {
			fmt.Fprint(w, `    <ul class="approvals-list">
`)
			for _, a := range pendingApprovals {
				// Format confidence score as percentage (only show if > 0)
				confidenceStr := ""
				if a.ConfidenceScore > 0 {
					confidenceStr = fmt.Sprintf(`<span class="confidence">confidence: %.0f%%</span>`, a.ConfidenceScore*100)
				}
				riskStr := ""
				if a.RiskScore > 0 {
					riskStr = fmt.Sprintf(`<span class="risk">risk: %.0f%%</span>`, a.RiskScore*100)
				}
				createdShort := a.CreatedAt
				if len(createdShort) > 16 {
					createdShort = createdShort[:16]
				}
				fmt.Fprintf(w, `        <li class="approval-item" id="approval-%s">
            <div class="approval-meta">
                <div class="approval-title">%s</div>
                <div class="approval-detail">agent: %s &middot; domain: %s &middot; type: %s &middot; %s</div>
            </div>
            <div class="approval-scores">%s %s</div>
            <div class="approval-actions">
                <form method="POST" action="/api/approvals/%s/approve" style="display:inline">
                    <input type="hidden" name="user" value="dashboard">
                    <button type="submit"
                            hx-post="/api/approvals/%s/approve"
                            hx-vals='{"user":"dashboard"}'
                            hx-target="#approval-%s"
                            hx-swap="outerHTML"
                            class="btn-approve">&#10003; Approve</button>
                </form>
                <form method="POST" action="/api/approvals/%s/reject" style="display:inline">
                    <input type="hidden" name="user" value="dashboard">
                    <button type="submit"
                            hx-post="/api/approvals/%s/reject"
                            hx-vals='{"user":"dashboard"}'
                            hx-target="#approval-%s"
                            hx-swap="outerHTML"
                            class="btn-reject">&#10007; Reject</button>
                </form>
            </div>
        </li>
`, a.ID, a.Title, a.AgentID, a.Domain, a.Type, createdShort,
					confidenceStr, riskStr,
					a.ID, a.ID, a.ID,
					a.ID, a.ID, a.ID)
			}
			fmt.Fprint(w, `    </ul>
`)
		}
	}

	fmt.Fprint(w, `    </div>
</body>
</html>
`)
}
