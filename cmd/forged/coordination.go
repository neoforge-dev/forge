package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"time"
)

// CoordinationDashboard tracks sprint progress
type CoordinationDashboard struct {
	db *sql.DB
}

// SprintStatus represents the current state of a sprint
type SprintStatus struct {
	Name       string              `json:"name"`
	StartedAt  time.Time           `json:"started_at"`
	Agents     []AgentSprintStatus `json:"agents"`
	Commits    CommitSummary       `json:"commits"`
	Blockers   []Blocker           `json:"blockers"`
	Completion float64             `json:"completion_percent"`
	ETA        string              `json:"eta"`
}

// AgentSprintStatus represents an agent's current work status during a sprint
type AgentSprintStatus struct {
	Name         string    `json:"name"`
	CurrentTask  string    `json:"current_task"`
	Status       string    `json:"status"` // idle, working, blocked
	LastCommit   time.Time `json:"last_commit"`
	CommitCount  int       `json:"commit_count"`
	LinesChanged int       `json:"lines_changed"`
}

// CommitSummary provides aggregate commit statistics
type CommitSummary struct {
	Total       int      `json:"total"`
	LastHour    int      `json:"last_hour"`
	Authors     []string `json:"authors"`
	AvgInterval string   `json:"avg_interval"`
}

// Blocker represents an impediment to progress
type Blocker struct {
	Agent       string    `json:"agent"`
	Description string    `json:"description"`
	Since       time.Time `json:"since"`
	Severity    string    `json:"severity"`
}

// NewCoordinationDashboard creates a new coordination dashboard
func NewCoordinationDashboard(db *sql.DB) *CoordinationDashboard {
	return &CoordinationDashboard{db: db}
}

// GetSprintStatus retrieves the current status of a sprint
func (cd *CoordinationDashboard) GetSprintStatus(sprintName string) (*SprintStatus, error) {
	// Default sprint name
	if sprintName == "" || sprintName == "current" {
		sprintName = "Phase 3"
	}

	status := &SprintStatus{
		Name:      sprintName,
		StartedAt: time.Now().Add(-2 * time.Hour), // Assume 2h ago for now
	}

	// Get agent statuses from database and git
	status.Agents = cd.getAgentStatuses(sprintName)

	// Get commit summary
	status.Commits = cd.getCommitSummary(sprintName)

	// Detect blockers
	status.Blockers = cd.detectBlockers(status.Agents)

	// Calculate completion
	status.Completion = cd.calculateCompletion(status)

	// Estimate completion
	status.ETA = cd.estimateETA(status)

	return status, nil
}

// getAgentStatuses retrieves agent activity from git and database
func (cd *CoordinationDashboard) getAgentStatuses(sprintName string) []AgentSprintStatus {
	var agents []AgentSprintStatus

	// Query recent commits from git
	// Format: hash|author|date|subject
	cmd := exec.Command("git", "log", "--since=4 hours ago",
		"--format=%H|%an|%ai|%s",
		"--numstat")
	output, err := cmd.Output()
	if err != nil {
		// Git command failed, return empty
		return agents
	}

	// Parse commits
	agentMap := make(map[string]*AgentSprintStatus)
	lines := strings.Split(string(output), "\n")
	var currentCommit *commitInfo

	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}

		// Check if this is a commit header line
		if strings.Contains(line, "|") {
			parts := strings.SplitN(line, "|", 4)
			if len(parts) >= 3 {
				author := parts[1]
				dateStr := parts[2]
				subject := ""
				if len(parts) >= 4 {
					subject = parts[3]
				}

				commitTime, _ := time.Parse("2006-01-02 15:04:05 -0700", dateStr)

				currentCommit = &commitInfo{
					author:  author,
					date:    commitTime,
					subject: subject,
				}

				if _, exists := agentMap[author]; !exists {
					agentMap[author] = &AgentSprintStatus{
						Name:        author,
						Status:      "working",
						CurrentTask: subject,
						LastCommit:  commitTime,
					}
				} else {
					// Update last commit if newer
					if commitTime.After(agentMap[author].LastCommit) {
						agentMap[author].LastCommit = commitTime
						agentMap[author].CurrentTask = subject
					}
				}
				agentMap[author].CommitCount++
			}
		} else if currentCommit != nil {
			// This is a file stat line (added\tremoved\tfile)
			parts := strings.Fields(line)
			if len(parts) >= 2 {
				// Try to parse lines added/removed
				var added, removed int
				fmt.Sscanf(parts[0], "%d", &added)
				fmt.Sscanf(parts[1], "%d", &removed)
				if agent, exists := agentMap[currentCommit.author]; exists {
					agent.LinesChanged += added + removed
				}
			}
		}
	}

	// Also get from database heartbeats
	dbAgents := cd.getAgentStatusesFromDB()
	for _, dbAgent := range dbAgents {
		if existing, exists := agentMap[dbAgent.Name]; exists {
			// Merge info
			if dbAgent.CurrentTask != "" {
				existing.CurrentTask = dbAgent.CurrentTask
			}
			if dbAgent.Status != "" {
				existing.Status = dbAgent.Status
			}
		} else {
			agentMap[dbAgent.Name] = &dbAgent
		}
	}

	// Convert map to slice
	for _, agent := range agentMap {
		// Update status based on last activity
		if time.Since(agent.LastCommit) > 30*time.Minute {
			agent.Status = "idle"
		}
		if time.Since(agent.LastCommit) > 60*time.Minute {
			agent.Status = "blocked"
		}
		agents = append(agents, *agent)
	}

	return agents
}

// commitInfo holds temporary commit data during parsing
type commitInfo struct {
	author  string
	date    time.Time
	subject string
}

// getAgentStatusesFromDB retrieves agent info from heartbeat database
func (cd *CoordinationDashboard) getAgentStatusesFromDB() []AgentSprintStatus {
	var agents []AgentSprintStatus

	rows, err := cd.db.Query(`
		SELECT agent_id, current_task_id, status, last_seen, context_pct
		FROM agent_heartbeats
		WHERE last_seen > datetime('now', '-1 hour')
		ORDER BY last_seen DESC
	`)
	if err != nil {
		return agents
	}
	defer rows.Close()

	for rows.Next() {
		var agent AgentSprintStatus
		var lastSeen string
		var contextPct float64

		err := rows.Scan(&agent.Name, &agent.CurrentTask, &agent.Status, &lastSeen, &contextPct)
		if err != nil {
			continue
		}

		// Parse timestamp
		agent.LastCommit, _ = time.Parse("2006-01-02 15:04:05", lastSeen)

		// Set status based on context and activity
		if contextPct > 80 {
			agent.Status = "high_context"
		}

		agents = append(agents, agent)
	}

	return agents
}

// getCommitSummary generates aggregate commit statistics
func (cd *CoordinationDashboard) getCommitSummary(sprintName string) CommitSummary {
	summary := CommitSummary{}

	// Total commits in last 4 hours
	cmd := exec.Command("git", "log", "--since=4 hours ago", "--oneline")
	output, err := cmd.Output()
	if err == nil {
		lines := strings.Split(string(output), "\n")
		for _, line := range lines {
			if strings.TrimSpace(line) != "" {
				summary.Total++
			}
		}
	}

	// Last hour commits
	cmd = exec.Command("git", "log", "--since=1 hour ago", "--oneline")
	output, err = cmd.Output()
	if err == nil {
		lines := strings.Split(string(output), "\n")
		for _, line := range lines {
			if strings.TrimSpace(line) != "" {
				summary.LastHour++
			}
		}
	}

	// Authors in last 4 hours
	cmd = exec.Command("git", "log", "--since=4 hours ago", "--format=%an")
	output, err = cmd.Output()
	if err == nil {
		authorMap := make(map[string]bool)
		for _, author := range strings.Split(string(output), "\n") {
			if author != "" {
				authorMap[author] = true
			}
		}
		for author := range authorMap {
			summary.Authors = append(summary.Authors, author)
		}
	}

	// Calculate average commit interval
	if summary.Total > 1 {
		cmd = exec.Command("git", "log", "--since=4 hours ago", "--format=%ai")
		output, err = cmd.Output()
		if err == nil {
			lines := strings.Split(string(output), "\n")
			if len(lines) >= 2 {
				first, _ := time.Parse("2006-01-02 15:04:05 -0700", strings.TrimSpace(lines[0]))
				last, _ := time.Parse("2006-01-02 15:04:05 -0700", strings.TrimSpace(lines[len(lines)-2]))
				if !first.IsZero() && !last.IsZero() {
					duration := first.Sub(last)
					avgMinutes := duration.Minutes() / float64(summary.Total-1)
					summary.AvgInterval = fmt.Sprintf("%.1f min", avgMinutes)
				}
			}
		}
	} else {
		summary.AvgInterval = "N/A"
	}

	return summary
}

// detectBlockers identifies agents who may be stuck
func (cd *CoordinationDashboard) detectBlockers(agents []AgentSprintStatus) []Blocker {
	var blockers []Blocker

	for _, agent := range agents {
		// Detect if agent hasn't committed in 30+ minutes
		sinceLastCommit := time.Since(agent.LastCommit)

		if sinceLastCommit > 30*time.Minute && sinceLastCommit <= 60*time.Minute {
			blockers = append(blockers, Blocker{
				Agent:       agent.Name,
				Description: fmt.Sprintf("No commits in %d minutes", int(sinceLastCommit.Minutes())),
				Since:       agent.LastCommit,
				Severity:    "warning",
			})
		} else if sinceLastCommit > 60*time.Minute {
			blockers = append(blockers, Blocker{
				Agent:       agent.Name,
				Description: fmt.Sprintf("No commits in %d minutes - may be blocked", int(sinceLastCommit.Minutes())),
				Since:       agent.LastCommit,
				Severity:    "critical",
			})
		}

		// Check for high context usage (potential handoff needed)
		if agent.Status == "high_context" {
			blockers = append(blockers, Blocker{
				Agent:       agent.Name,
				Description: "High context usage - handoff recommended",
				Since:       agent.LastCommit,
				Severity:    "warning",
			})
		}
	}

	return blockers
}

// calculateCompletion estimates sprint completion percentage
func (cd *CoordinationDashboard) calculateCompletion(status *SprintStatus) float64 {
	// Simple heuristic based on commit velocity and agent activity
	// In a real implementation, this would check against actual task completion

	baseCompletion := 0.0

	// Factor 1: Number of active agents (max 30%)
	activeAgents := 0
	for _, agent := range status.Agents {
		if agent.Status == "working" {
			activeAgents++
		}
	}
	baseCompletion += float64(activeAgents) * 5.0
	if baseCompletion > 30 {
		baseCompletion = 30
	}

	// Factor 2: Commit velocity (max 40%)
	if status.Commits.Total > 0 {
		commitFactor := float64(status.Commits.Total) * 2.0
		if commitFactor > 40 {
			commitFactor = 40
		}
		baseCompletion += commitFactor
	}

	// Factor 3: No blockers (max 30%)
	if len(status.Blockers) == 0 {
		baseCompletion += 30
	} else {
		// Reduce completion for each blocker
		penalty := float64(len(status.Blockers)) * 5.0
		if penalty > 30 {
			penalty = 30
		}
		baseCompletion += (30 - penalty)
	}

	return baseCompletion
}

// estimateETA calculates estimated time to completion
func (cd *CoordinationDashboard) estimateETA(status *SprintStatus) string {
	if status.Completion >= 100 {
		return "Complete"
	}

	if status.Completion <= 0 {
		return "Unknown"
	}

	// Calculate velocity (percent per hour)
	timeRunning := time.Since(status.StartedAt).Hours()
	if timeRunning <= 0 {
		return "Calculating..."
	}

	velocity := status.Completion / timeRunning
	if velocity <= 0 {
		return "Stalled"
	}

	remaining := 100 - status.Completion
	hoursRemaining := remaining / velocity

	if hoursRemaining < 1 {
		return fmt.Sprintf("%.0f minutes", hoursRemaining*60)
	}
	return fmt.Sprintf("%.1f hours", hoursRemaining)
}

// ServeHTTP handles HTTP requests for coordination status
func (cd *CoordinationDashboard) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	sprintName := r.URL.Query().Get("sprint")

	status, err := cd.GetSprintStatus(sprintName)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	// Blocker alerting: log and optionally notify webhook
	if len(status.Blockers) > 0 {
		log.Printf("[Coordination] Blockers detected: %d - %s", len(status.Blockers), sprintName)
		for _, b := range status.Blockers {
			log.Printf("[Coordination] Blocker: %s - %s (severity: %s)", b.Agent, b.Description, b.Severity)
		}
		go cd.sendBlockerAlert(status.Blockers)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-cache")
	json.NewEncoder(w).Encode(status)
}

// sendBlockerAlert logs blockers and optionally POSTs to FORGE_COORDINATION_BLOCKER_WEBHOOK_URL
func (cd *CoordinationDashboard) sendBlockerAlert(blockers []Blocker) {
	webhookURL := os.Getenv("FORGE_COORDINATION_BLOCKER_WEBHOOK_URL")
	if webhookURL == "" {
		return
	}
	body, err := json.Marshal(map[string]interface{}{
		"blockers": blockers,
		"timestamp": time.Now().Format(time.RFC3339),
	})
	if err != nil {
		log.Printf("[Coordination] Failed to marshal blocker alert: %v", err)
		return
	}
	resp, err := http.Post(webhookURL, "application/json", bytes.NewReader(body))
	if err != nil {
		log.Printf("[Coordination] Blocker webhook POST failed: %v", err)
		return
	}
	resp.Body.Close()
	if resp.StatusCode >= 400 {
		log.Printf("[Coordination] Blocker webhook returned %d", resp.StatusCode)
	}
}

// GetCoordinationHTML returns HTML for dashboard integration
func (cd *CoordinationDashboard) GetCoordinationHTML() string {
	status, _ := cd.GetSprintStatus("current")

	html := `<div class="section">
<div class="section-header">🎯 Sprint Coordination: ` + status.Name + `</div>
<div class="metrics-grid">
<div class="metric-card">
<div class="metric-value">` + fmt.Sprintf("%.0f", status.Completion) + `%</div>
<div class="metric-label">Completion</div>
</div>
<div class="metric-card">
<div class="metric-value">` + fmt.Sprintf("%d", len(status.Agents)) + `</div>
<div class="metric-label">Active Agents</div>
</div>
<div class="metric-card">
<div class="metric-value">` + fmt.Sprintf("%d", status.Commits.Total) + `</div>
<div class="metric-label">Commits (4h)</div>
</div>
<div class="metric-card">
<div class="metric-value">` + status.ETA + `</div>
<div class="metric-label">Est. Completion</div>
</div>
</div>`

	// Blockers section
	if len(status.Blockers) > 0 {
		html += `<div style="padding: 1rem;"><h4 style="color: #f0883e;">⚠️ Active Blockers</h4>`
		for _, blocker := range status.Blockers {
			severityColor := "#f0883e"
			if blocker.Severity == "critical" {
				severityColor = "#da3633"
			}
			html += `<div style="padding: 0.5rem; background: #21262d; border-left: 3px solid ` + severityColor + `; margin-bottom: 0.5rem;">
<strong>` + blocker.Agent + `:</strong> ` + blocker.Description + `
<span style="color: #8b949e; float: right;">` + time.Since(blocker.Since).Round(time.Minute).String() + ` ago</span>
</div>`
		}
		html += `</div>`
	}

	// Agents section
	html += `<div style="padding: 1rem;"><h4>👥 Agent Status</h4>
<table style="width: 100%; border-collapse: collapse;">
<thead><tr style="border-bottom: 1px solid #30363d;">
<th style="text-align: left; padding: 0.5rem;">Agent</th>
<th style="text-align: left; padding: 0.5rem;">Status</th>
<th style="text-align: left; padding: 0.5rem;">Current Task</th>
<th style="text-align: right; padding: 0.5rem;">Commits</th>
<th style="text-align: right; padding: 0.5rem;">Last Activity</th>
</tr></thead><tbody>`

	for _, agent := range status.Agents {
		statusColor := "#3fb950" // working/green
		if agent.Status == "idle" {
			statusColor = "#8b949e"
		} else if agent.Status == "blocked" || agent.Status == "high_context" {
			statusColor = "#da3633"
		}

		lastActivity := time.Since(agent.LastCommit).Round(time.Minute).String()
		if agent.LastCommit.IsZero() {
			lastActivity = "Unknown"
		}

		html += `<tr style="border-bottom: 1px solid #30363d;">
<td style="padding: 0.5rem;"><strong>` + agent.Name + `</strong></td>
<td style="padding: 0.5rem;"><span style="color: ` + statusColor + `;">` + agent.Status + `</span></td>
<td style="padding: 0.5rem;">` + truncateString(agent.CurrentTask, 40) + `</td>
<td style="padding: 0.5rem; text-align: right;">` + fmt.Sprintf("%d", agent.CommitCount) + `</td>
<td style="padding: 0.5rem; text-align: right; color: #8b949e;">` + lastActivity + ` ago</td>
</tr>`
	}

	html += `</tbody></table></div></div>`

	return html
}

// truncateString truncates a string to max length with ellipsis
func truncateString(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen-3] + "..."
}
