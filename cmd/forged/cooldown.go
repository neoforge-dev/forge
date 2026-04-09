//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"time"
)

// CooldownEntry represents an agent's cooldown state.
type CooldownEntry struct {
	AgentID       string `json:"agent_id"`
	Reason        string `json:"reason"`
	CooldownUntil string `json:"cooldown_until"`
	CreatedAt     string `json:"created_at"`
}

// SetAgentCooldown puts an agent into cooldown for the given duration.
func SetAgentCooldown(db *sql.DB, agentID, reason string, duration time.Duration) error {
	until := time.Now().UTC().Add(duration).Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO agent_cooldowns (agent_id, reason, cooldown_until, created_at)
		VALUES (?, ?, ?, datetime('now'))
		ON CONFLICT(agent_id) DO UPDATE SET
			reason = excluded.reason,
			cooldown_until = excluded.cooldown_until,
			created_at = datetime('now')
	`, agentID, reason, until)
	return err
}

// IsAgentInCooldown returns true if the agent is currently in cooldown.
func IsAgentInCooldown(db *sql.DB, agentID string) (bool, error) {
	var count int
	err := db.QueryRow(`
		SELECT COUNT(*) FROM agent_cooldowns
		WHERE agent_id = ? AND cooldown_until > datetime('now')
	`, agentID).Scan(&count)
	if err != nil {
		return false, err
	}
	return count > 0, nil
}

// ClearAgentCooldown removes an agent's cooldown entry.
func ClearAgentCooldown(db *sql.DB, agentID string) error {
	_, err := db.Exec(`DELETE FROM agent_cooldowns WHERE agent_id = ?`, agentID)
	return err
}

// GetCooldownAgents returns all agents currently in cooldown.
func GetCooldownAgents(db *sql.DB) ([]CooldownEntry, error) {
	rows, err := db.Query(`
		SELECT agent_id, reason, cooldown_until, created_at
		FROM agent_cooldowns
		WHERE cooldown_until > datetime('now')
		ORDER BY cooldown_until ASC
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var entries []CooldownEntry
	for rows.Next() {
		var e CooldownEntry
		if err := rows.Scan(&e.AgentID, &e.Reason, &e.CooldownUntil, &e.CreatedAt); err != nil {
			return nil, err
		}
		entries = append(entries, e)
	}
	return entries, rows.Err()
}

// PurgeExpiredCooldowns removes cooldown entries that have expired.
func PurgeExpiredCooldowns(db *sql.DB) (int64, error) {
	result, err := db.Exec(`DELETE FROM agent_cooldowns WHERE cooldown_until <= datetime('now')`)
	if err != nil {
		return 0, err
	}
	return result.RowsAffected()
}
