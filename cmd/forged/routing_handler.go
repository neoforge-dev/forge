//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http"
)

// routingResolveRequest is the JSON body for POST /api/routing/resolve.
type routingResolveRequest struct {
	TaskType       string   `json:"task_type"`
	Weight         string   `json:"weight"`
	ForbiddenNodes []string `json:"forbidden_nodes"`
	PortfolioStage string   `json:"portfolio_stage"`
}

// routingResolveResponse is the JSON body returned on success.
type routingResolveResponse struct {
	Agent        string `json:"agent"`
	Node         string `json:"node"`
	Reason       string `json:"reason"`
	RequiredTier string `json:"required_tier,omitempty"` // "phone" = human gate required, "watch" = auto-approve
}

// routingErrorResponse is the JSON body returned on error.
type routingErrorResponse struct {
	Error string `json:"error"`
}

// routingResolveHandler handles POST /api/routing/resolve.
//
// It reads the routing YAML config files on every call (no caching), resolves
// the best agent for the requested task type + weight, and returns the result.
//
// Request JSON (all fields optional):
//
//	{"task_type": "coverage", "weight": "light", "forbidden_nodes": ["prya"]}
//
// Response 200:
//
//	{"agent": "kimi", "node": "sati", "reason": "best_at match: go-test"}
//
// Response 400: invalid request body.
// Response 503: no eligible agent found or config unavailable.
func routingResolveHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Content-Type", "application/json")

	var req routingResolveRequest
	if r.Body != nil && r.ContentLength != 0 {
		dec := json.NewDecoder(r.Body)
		dec.DisallowUnknownFields()
		if err := dec.Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(routingErrorResponse{Error: "invalid request body: " + err.Error()})
			return
		}
	}

	// Apply defaults.
	if req.Weight == "" {
		req.Weight = "light"
	}

	// Load routing bundle fresh on every request (low-frequency endpoint).
	configDir := routingConfigDir()
	bundle, err := LoadRoutingBundle(configDir)
	if err != nil {
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(routingErrorResponse{
			Error: "routing config unavailable: " + err.Error(),
		})
		return
	}

	agent, node, reason, err := ResolveAgent(bundle, req.TaskType, req.Weight, req.ForbiddenNodes, req.PortfolioStage)
	if err != nil {
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(routingErrorResponse{Error: "no eligible agent found"})
		return
	}

	// Stage-based approval tier: deploy-stage projects require human notification.
	requiredTier := "watch"
	if req.PortfolioStage == "deploy" {
		requiredTier = "phone"
	}

	json.NewEncoder(w).Encode(routingResolveResponse{
		Agent:        agent,
		Node:         node,
		Reason:       reason,
		RequiredTier: requiredTier,
	})
}
