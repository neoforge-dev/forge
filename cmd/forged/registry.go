//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"sync"
	"time"
)

// Agent represents a connected agent in the fleet.
type Agent struct {
	ID           string
	Name         string
	Node         string
	Role         string
	Tier         string
	Status       string
	ContextPct   float64
	Capabilities []string
	LastActivity time.Time
}

// AgentRegistry provides thread-safe access to agent state backed by SQLite.
type AgentRegistry struct {
	db          *sql.DB
	mu          sync.RWMutex
	agents      map[string]*Agent
	staleAfter  time.Duration
	cleanupStop chan struct{}
	cleanupOnce sync.Once
}

// NewAgentRegistry creates a new registry and hydrates it from the SQLite agents table.
func NewAgentRegistry(db *sql.DB, staleAfter time.Duration) *AgentRegistry {
	if staleAfter <= 0 {
		staleAfter = 5 * time.Minute
	}

	r := &AgentRegistry{
		db:          db,
		agents:      make(map[string]*Agent),
		staleAfter:  staleAfter,
		cleanupStop: make(chan struct{}),
	}

	if err := r.loadFromDB(); err != nil {
		log.Printf("agent registry: failed to load from db: %v", err)
	}

	return r
}

// loadFromDB initializes the in-memory cache from the agents table.
func (r *AgentRegistry) loadFromDB() error {
	rows, err := r.db.Query(`SELECT id, name, node, role, tier, status, context_pct, capabilities, last_activity FROM agents`)
	if err != nil {
		return fmt.Errorf("query agents: %w", err)
	}
	defer rows.Close()

	now := time.Now().UTC()

	for rows.Next() {
		var (
			id, name, node, role, tier, status string
			contextPct                         float64
			capsJSON                           sql.NullString
			lastActivityStr                    sql.NullString
		)

		if err := rows.Scan(&id, &name, &node, &role, &tier, &status, &contextPct, &capsJSON, &lastActivityStr); err != nil {
			return fmt.Errorf("scan agent: %w", err)
		}

		var caps []string
		if capsJSON.Valid && capsJSON.String != "" {
			_ = json.Unmarshal([]byte(capsJSON.String), &caps)
		}

		var lastActivity time.Time
		if lastActivityStr.Valid && lastActivityStr.String != "" {
			if t, err := time.Parse(time.RFC3339, lastActivityStr.String); err == nil {
				lastActivity = t
			}
		}
		if lastActivity.IsZero() {
			lastActivity = now
		}

		a := &Agent{
			ID:           id,
			Name:         name,
			Node:         node,
			Role:         role,
			Tier:         tier,
			Status:       status,
			ContextPct:   contextPct,
			Capabilities: caps,
			LastActivity: lastActivity,
		}

		r.agents[a.ID] = a
	}

	return rows.Err()
}

// Register inserts or updates an agent in the registry and SQLite.
func (r *AgentRegistry) Register(agent *Agent) error {
	if agent == nil || agent.ID == "" {
		return fmt.Errorf("agent must have an ID")
	}

	if agent.LastActivity.IsZero() {
		agent.LastActivity = time.Now().UTC()
	}

	capsJSON, err := json.Marshal(agent.Capabilities)
	if err != nil {
		return fmt.Errorf("marshal capabilities: %w", err)
	}

	_, err = r.db.Exec(`
INSERT INTO agents (id, name, node, role, tier, status, context_pct, current_task_id, capabilities, last_activity, registered_at)
VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, COALESCE((
    SELECT registered_at FROM agents WHERE id = ?
), datetime('now')))
ON CONFLICT(id) DO UPDATE SET
    name = excluded.name,
    node = excluded.node,
    role = excluded.role,
    tier = excluded.tier,
    status = excluded.status,
    context_pct = excluded.context_pct,
    capabilities = excluded.capabilities,
    last_activity = excluded.last_activity;
`, agent.ID, agent.Name, agent.Node, agent.Role, agent.Tier, agent.Status, agent.ContextPct, string(capsJSON), agent.LastActivity.UTC().Format(time.RFC3339), agent.ID)
	if err != nil {
		return fmt.Errorf("upsert agent: %w", err)
	}

	r.mu.Lock()
	defer r.mu.Unlock()
	r.agents[agent.ID] = &Agent{
		ID:           agent.ID,
		Name:         agent.Name,
		Node:         agent.Node,
		Role:         agent.Role,
		Tier:         agent.Tier,
		Status:       agent.Status,
		ContextPct:   agent.ContextPct,
		Capabilities: append([]string(nil), agent.Capabilities...),
		LastActivity: agent.LastActivity,
	}

	return nil
}

// Unregister removes an agent from the registry and SQLite.
func (r *AgentRegistry) Unregister(agentID string) error {
	if agentID == "" {
		return nil
	}

	if _, err := r.db.Exec(`DELETE FROM agents WHERE id = ?`, agentID); err != nil {
		return fmt.Errorf("delete agent: %w", err)
	}

	r.mu.Lock()
	defer r.mu.Unlock()
	delete(r.agents, agentID)

	return nil
}

// UpdateStatus updates an agent's status field.
func (r *AgentRegistry) UpdateStatus(agentID, status string) error {
	if agentID == "" {
		return nil
	}

	if _, err := r.db.Exec(`UPDATE agents SET status = ? WHERE id = ?`, status, agentID); err != nil {
		return fmt.Errorf("update status: %w", err)
	}

	r.mu.Lock()
	defer r.mu.Unlock()
	if a, ok := r.agents[agentID]; ok {
		a.Status = status
	}

	return nil
}

// UpdateContext updates an agent's context percentage.
func (r *AgentRegistry) UpdateContext(agentID string, pct float64) error {
	if agentID == "" {
		return nil
	}

	if pct < 0 {
		pct = 0
	}
	if pct > 100 {
		pct = 100
	}

	if _, err := r.db.Exec(`UPDATE agents SET context_pct = ? WHERE id = ?`, pct, agentID); err != nil {
		return fmt.Errorf("update context_pct: %w", err)
	}

	r.mu.Lock()
	defer r.mu.Unlock()
	if a, ok := r.agents[agentID]; ok {
		a.ContextPct = pct
	}

	return nil
}

// Heartbeat records a heartbeat for the given agent and updates last_activity, status, and context_pct.
func (r *AgentRegistry) Heartbeat(agentID, status string, pct float64) error {
	if agentID == "" {
		return nil
	}

	if pct < 0 {
		pct = 0
	}
	if pct > 100 {
		pct = 100
	}

	now := time.Now().UTC()

	if _, err := r.db.Exec(
		`UPDATE agents SET status = ?, context_pct = ?, last_activity = ? WHERE id = ?`,
		status,
		pct,
		now.Format(time.RFC3339),
		agentID,
	); err != nil {
		return fmt.Errorf("update heartbeat: %w", err)
	}

	r.mu.Lock()
	defer r.mu.Unlock()

	if a, ok := r.agents[agentID]; ok {
		a.Status = status
		a.ContextPct = pct
		a.LastActivity = now
	}

	return nil
}

// GetAgent returns a copy of the agent with the given ID.
func (r *AgentRegistry) GetAgent(agentID string) (*Agent, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	a, ok := r.agents[agentID]
	if !ok {
		return nil, false
	}

	copy := *a
	copy.Capabilities = append([]string(nil), a.Capabilities...)
	return &copy, true
}

// ListAgents returns copies of all agents.
func (r *AgentRegistry) ListAgents() []*Agent {
	r.mu.RLock()
	defer r.mu.RUnlock()

	out := make([]*Agent, 0, len(r.agents))
	for _, a := range r.agents {
		copy := *a
		copy.Capabilities = append([]string(nil), a.Capabilities...)
		out = append(out, &copy)
	}
	return out
}

// GetActiveAgents returns agents whose status is "active".
func (r *AgentRegistry) GetActiveAgents() []*Agent {
	r.mu.RLock()
	defer r.mu.RUnlock()

	var out []*Agent
	for _, a := range r.agents {
		if a.Status == "active" {
			copy := *a
			copy.Capabilities = append([]string(nil), a.Capabilities...)
			out = append(out, &copy)
		}
	}
	return out
}

// StartCleanup launches a background goroutine that removes stale agents.
func (r *AgentRegistry) StartCleanup(interval time.Duration) {
	if interval <= 0 {
		interval = time.Minute
	}

	r.cleanupOnce.Do(func() {
		go func() {
			ticker := time.NewTicker(interval)
			defer ticker.Stop()

			for {
				select {
				case <-ticker.C:
					if err := r.cleanupStaleAgents(); err != nil {
						log.Printf("agent registry cleanup error: %v", err)
					}
				case <-r.cleanupStop:
					return
				}
			}
		}()
	})
}

// StopCleanup stops the background cleanup goroutine.
func (r *AgentRegistry) StopCleanup() {
	close(r.cleanupStop)
}

// cleanupStaleAgents removes agents whose last_activity is older than staleAfter.
func (r *AgentRegistry) cleanupStaleAgents() error {
	cutoff := time.Now().UTC().Add(-r.staleAfter)

	// Remove from DB first.
	if _, err := r.db.Exec(`DELETE FROM agents WHERE last_activity IS NOT NULL AND last_activity < ?`, cutoff.Format(time.RFC3339)); err != nil {
		return fmt.Errorf("delete stale agents: %w", err)
	}

	// Then remove from memory.
	r.mu.Lock()
	defer r.mu.Unlock()

	for id, a := range r.agents {
		if a.LastActivity.Before(cutoff) {
			delete(r.agents, id)
		}
	}

	return nil
}
