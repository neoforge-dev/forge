//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"time"
)

// WatchAction represents a pending decision presented to the Watch UI.
type WatchAction struct {
	ID      string `json:"id"`
	Type    string `json:"type"`
	Title   string `json:"title"`
	Tier    string `json:"tier"`
	AgentID string `json:"agent_id"`
	Domain  string `json:"domain"`
	// Buttons is the set of action labels shown as Watch action buttons.
	Buttons []string `json:"buttons"`
}

// WatchSummary is the payload returned by GET /api/watch/summary.
type WatchSummary struct {
	AgentsOnline      int           `json:"agents_online"`
	AgentsTotal       int           `json:"agents_total"`
	TasksRunning      int           `json:"tasks_running"`
	GatesPending      int           `json:"gates_pending"`
	MRR               float64       `json:"mrr"`
	PendingDecisions  []WatchAction `json:"pending_decisions"`
	LastUpdated       time.Time     `json:"last_updated"`
}

// watchActionButtons returns appropriate Watch action button labels for an approval tier.
func watchActionButtons(tier string) []string {
	switch tier {
	case string(TierWatch):
		return []string{"approve"}
	case string(TierPhone):
		return []string{"approve", "reject"}
	case string(TierDesktop):
		return []string{"approve", "reject", "defer"}
	default:
		return []string{"approve", "reject"}
	}
}

// buildWatchSummary queries the DB and approval service to build a WatchSummary.
func buildWatchSummary(ctx context.Context, svc *ApprovalService) WatchSummary {
	db := getDBConn()
	fc := getFleetCounts(ctx, db)

	summary := WatchSummary{
		AgentsOnline: fc.OnlineAgents,
		AgentsTotal:  fc.TotalAgents,
		TasksRunning: fc.RunningTasks,
		MRR:          0.0,
		LastUpdated:  time.Now().UTC(),
	}

	// Fetch pending approvals for the Watch UI.
	var pendingDecisions []WatchAction
	if svc != nil {
		pending, err := svc.GetPending(ctx, 20)
		if err == nil {
			for _, a := range pending {
				wa := WatchAction{
					ID:      a.ID,
					Type:    string(a.Type),
					Title:   a.Title,
					Tier:    string(a.Tier),
					AgentID: a.AgentID,
					Domain:  a.Domain,
					Buttons: watchActionButtons(string(a.Tier)),
				}
				pendingDecisions = append(pendingDecisions, wa)
			}
		}
	}
	if pendingDecisions == nil {
		pendingDecisions = []WatchAction{}
	}
	summary.GatesPending = len(pendingDecisions)
	summary.PendingDecisions = pendingDecisions

	return summary
}

// globalApprovalServiceForWatch holds a reference set at daemon startup.
// handlers_watch.go uses this to avoid re-initializing the service on each request.
// The orchestrator sets globalApprovalService; we reuse it.
var watchSummaryApprovalService *ApprovalService

// watchSummaryHandler handles GET /api/watch/summary.
func watchSummaryHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	summary := buildWatchSummary(r.Context(), watchSummaryApprovalService)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(summary)
}
