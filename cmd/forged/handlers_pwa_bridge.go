//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http"
	"time"
)

// PWA Bridge Handlers (ADR-014 / ADR-028)

// --- Dashboard ---

type PWADashboardAgent struct {
	ID            string            `json:"id"`
	Role          string            `json:"role"`
	Project       *string           `json:"project"`
	Task          *string           `json:"task"`
	Status        string            `json:"status"`
	Progress      int               `json:"progress"`
	LastActivity  *string           `json:"last_activity"`
	IsStale       bool              `json:"is_stale"`
	Domain        *string           `json:"domain"`
	TokenUsage    map[string]int    `json:"token_usage"`
	MessagesCount int               `json:"messages_count"`
	FocusTags     []string          `json:"focus_tags"`
}

type PWATaskThroughput struct {
	TotalToday         int            `json:"total_today"`
	CompletedToday     int            `json:"completed_today"`
	FailedToday        int            `json:"failed_today"`
	InProgressToday    int            `json:"in_progress_today"`
	AvgDurationSeconds float64        `json:"avg_duration_seconds"`
	SuccessRate        float64        `json:"success_rate"`
	ByType             map[string]int `json:"by_type"`
}

type PWADashboardSummary struct {
	TotalAgents       int                 `json:"total_agents"`
	ActiveAgentsCount int                 `json:"active_agents_count"`
	IdleAgentsCount   int                 `json:"idle_agents_count"`
	ErrorAgentsCount  int                 `json:"error_agents_count"`
	PausedAgentsCount int                 `json:"paused_agents_count"`
	Agents            []PWADashboardAgent `json:"agents"`
	TaskThroughput    PWATaskThroughput   `json:"task_throughput"`
	SystemStatus      string              `json:"system_status"`
	LastUpdated       string              `json:"last_updated"`
}

type PWADashboardHandler struct {
	dashboard *Dashboard
}

func NewPWADashboardHandler(d *Dashboard) *PWADashboardHandler {
	return &PWADashboardHandler{dashboard: d}
}

func (h *PWADashboardHandler) RegisterRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/api/dashboard/summary", h.handleSummary)
	mux.HandleFunc("/api/dashboard/agents", h.handleAgents)
}

func (h *PWADashboardHandler) handleSummary(w http.ResponseWriter, r *http.Request) {
	if h.dashboard == nil || h.dashboard.db == nil {
		writeJSONError(w, http.StatusServiceUnavailable, "dashboard database not available")
		return
	}
	ctx := r.Context()
	data, err := h.dashboard.GetDashboardData(ctx)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}

	summary := PWADashboardSummary{
		TotalAgents:       data.Summary.TotalAgents,
		ActiveAgentsCount: data.Summary.BusyAgents,
		IdleAgentsCount:   data.Summary.IdleAgents,
		ErrorAgentsCount:  data.Summary.ErrorAgents,
		PausedAgentsCount: 0,
		SystemStatus:      data.Summary.SystemHealth,
		LastUpdated:       data.Timestamp.Format(time.RFC3339),
	}

	for _, a := range data.Agents {
		lastActivity := a.LastActivity.Format(time.RFC3339)
		summary.Agents = append(summary.Agents, PWADashboardAgent{
			ID:            a.AgentID,
			Role:          a.AgentID,
			Task:          a.CurrentTask,
			Status:        a.StatusText,
			Progress:      a.TaskProgress,
			LastActivity:  &lastActivity,
			IsStale:       a.Status == AgentStatusOffline,
			TokenUsage:    make(map[string]int),
			FocusTags:     a.Capabilities,
		})
	}

	summary.TaskThroughput = PWATaskThroughput{
		TotalToday:      data.Summary.TotalTasks,
		InProgressToday: data.Summary.ActiveTasks,
		SuccessRate:     100.0, // Default for now
		ByType:          make(map[string]int),
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"success": true, "data": summary})
}

func (h *PWADashboardHandler) handleAgents(w http.ResponseWriter, r *http.Request) {
	if h.dashboard == nil || h.dashboard.db == nil {
		writeJSONError(w, http.StatusServiceUnavailable, "dashboard database not available")
		return
	}
	ctx := r.Context()
	agents, err := h.dashboard.GetAgentHealth(ctx)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}

	var pwaAgents []PWADashboardAgent
	for _, a := range agents {
		lastActivity := a.LastActivity.Format(time.RFC3339)
		pwaAgents = append(pwaAgents, PWADashboardAgent{
			ID:            a.AgentID,
			Role:          a.AgentID,
			Status:        a.StatusText,
			Progress:      a.TaskProgress,
			LastActivity:  &lastActivity,
			IsStale:       a.Status == AgentStatusOffline,
			FocusTags:     a.Capabilities,
			TokenUsage:    make(map[string]int),
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"success": true, "data": pwaAgents})
}

// --- Fleet Summary ---

type PWAFleetSummaryNode struct {
	ID            string                `json:"id"`
	Name          string                `json:"name"`
	Status        string                `json:"status"`
	CPU           float64               `json:"cpu"`
	RAM           float64               `json:"ram"`
	Agents        []PWAFleetSummaryAgent `json:"agents"`
}

type PWAFleetSummaryAgent struct {
	Name       string  `json:"name"`
	Status     string  `json:"status"`
	ContextPct float64 `json:"context_pct,omitempty"`
}

type PWAFleetSummaryTasks struct {
	Total int `json:"total"`
	P0    int `json:"p0"`
	P1    int `json:"p1"`
	P2    int `json:"p2"`
}

type PWAFleetSummaryDispatch struct {
	Active         int `json:"active"`
	PendingResults int `json:"pending_results"`
}

type PWAFleetSummaryHealth struct {
	Errors1h    int `json:"errors_1h"`
	UptimeHours int `json:"uptime_hours"`
	TotalAgents int `json:"total_agents"`
	OnlineNodes int `json:"online_nodes"`
}

type PWAFleetSummary struct {
	Nodes           []PWAFleetSummaryNode   `json:"nodes"`
	TaskSummary     PWAFleetSummaryTasks    `json:"task_summary"`
	DispatchSummary PWAFleetSummaryDispatch `json:"dispatch_summary"`
	Health          PWAFleetSummaryHealth   `json:"health"`
}

func fleetSummaryHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	db := getDBConn()
	if db == nil {
		writeJSONError(w, http.StatusServiceUnavailable, "database not available")
		return
	}

	var totalTasks, p0, p1, p2 int
	db.QueryRowContext(r.Context(), "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('completed', 'abandoned', 'failed')").Scan(&totalTasks)
	db.QueryRowContext(r.Context(), "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('completed', 'abandoned', 'failed') AND priority >= 8").Scan(&p0)
	db.QueryRowContext(r.Context(), "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('completed', 'abandoned', 'failed') AND priority >= 5 AND priority < 8").Scan(&p1)
	db.QueryRowContext(r.Context(), "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('completed', 'abandoned', 'failed') AND priority < 5").Scan(&p2)

	var pendingResults int
	db.QueryRowContext(r.Context(), "SELECT COUNT(*) FROM tasks WHERE status = 'assigned'").Scan(&pendingResults)

	summary := PWAFleetSummary{
		TaskSummary: PWAFleetSummaryTasks{Total: totalTasks, P0: p0, P1: p1, P2: p2},
		DispatchSummary: PWAFleetSummaryDispatch{Active: pendingResults, PendingResults: pendingResults},
		Health: PWAFleetSummaryHealth{UptimeHours: 2, OnlineNodes: 1},
		Nodes: []PWAFleetSummaryNode{},
	}

	rows, err := db.QueryContext(r.Context(), "SELECT id, status FROM nodes")
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var id, status string
			if err := rows.Scan(&id, &status); err == nil {
				summary.Nodes = append(summary.Nodes, PWAFleetSummaryNode{ID: id, Name: id, Status: status, CPU: 0.1, RAM: 0.2})
				summary.Health.OnlineNodes++
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"success": true, "data": summary})
}

// --- Lead State ---

type PWALeadStateTransition struct {
	From   string `json:"from"`
	To     string `json:"to"`
	Reason string `json:"reason"`
	TS     string `json:"ts"`
}

type PWALeadState struct {
	Version          int                      `json:"version"`
	State            string                   `json:"state"`
	EnteredAt        string                   `json:"entered_at"`
	ContextPercent   float64                  `json:"context_percent"`
	CurrentSprint    string                   `json:"current_sprint"`
	CurrentWave      int                      `json:"current_wave"`
	AgentsDispatched []string                 `json:"agents_dispatched"`
	AgentsComplete   []string                 `json:"agents_complete"`
	PendingCommits   int                      `json:"pending_commits"`
	TransitionHistory []PWALeadStateTransition `json:"transition_history"`
	LastTransition   *PWALeadStateTransition `json:"last_transition"`
}

func leadStateHandler(w http.ResponseWriter, r *http.Request) {
	now := time.Now().Format(time.RFC3339)
	state := PWALeadState{
		Version:          1,
		State:            "ORCHESTRATING",
		EnteredAt:        now,
		ContextPercent:   62.7,
		CurrentSprint:    "S81",
		CurrentWave:      15,
		AgentsDispatched: []string{"glm"},
		AgentsComplete:   []string{"gemini", "codex", "kimi"},
		TransitionHistory: []PWALeadStateTransition{
			{From: "WAVE_START", To: "ORCHESTRATING", Reason: "heartbeat check", TS: now},
		},
	}
	state.LastTransition = &state.TransitionHistory[0]

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"success": true, "data": state})
}
