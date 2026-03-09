//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// tokenBudgetFile mirrors the CLI's tokenBudgetFile type.
// Authoritative source: .forge/heartbeat/token-budgets-{node}.json
type tokenBudgetFile struct {
	Node      string                    `json:"node"`
	UpdatedAt string                    `json:"updated_at"`
	Providers map[string]tbProviderData `json:"providers"`
}

type tbProviderData struct {
	Provider      string            `json:"provider"`
	Agents        []string          `json:"agents"`
	Limits        map[string]int    `json:"limits"`
	Resets        map[string]string `json:"resets"`
	Status        string            `json:"status"`
	CooldownUntil *string           `json:"cooldown_until,omitempty"`
}

// tokenBudgetFilePath returns the path to the per-node JSON file.
// forgeRoot defaults to "." if empty.
func tokenBudgetFilePath(forgeRoot, nodeID string) string {
	if forgeRoot == "" {
		forgeRoot = "."
	}
	return filepath.Join(forgeRoot, ".forge", "heartbeat",
		fmt.Sprintf("token-budgets-%s.json", nodeID))
}

// readTokenBudgetFile reads the per-node JSON budget file.
// Returns nil, nil if the file does not exist yet (not an error).
func readTokenBudgetFile(path string) (*tokenBudgetFile, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("read token budget file %s: %w", path, err)
	}
	var f tokenBudgetFile
	if err := json.Unmarshal(data, &f); err != nil {
		return nil, fmt.Errorf("parse token budget file %s: %w", path, err)
	}
	return &f, nil
}

// writeTokenBudgetFile writes the budget file back atomically (write-to-tmp, rename).
func writeTokenBudgetFile(path string, f *tokenBudgetFile) error {
	f.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	data, err := json.MarshalIndent(f, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal token budget: %w", err)
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0644); err != nil {
		return fmt.Errorf("write token budget tmp: %w", err)
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("rename token budget: %w", err)
	}
	return nil
}

// ensureTokenBudgetTables creates the migration-038 tables inline so the
// patrol works even if the SQL migration runner hasn't been applied yet.
func ensureTokenBudgetTables(ctx context.Context, db *sql.DB) error {
	_, err := db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS token_budget_log (
			id              TEXT PRIMARY KEY,
			node_id         TEXT NOT NULL,
			provider        TEXT NOT NULL,
			event_type      TEXT NOT NULL,
			monthly_pct     INTEGER,
			weekly_pct      INTEGER,
			h5_pct          INTEGER,
			status          TEXT NOT NULL,
			cooldown_until  TEXT,
			source          TEXT,
			recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
		);
		CREATE INDEX IF NOT EXISTS idx_token_budget_log_node_provider
			ON token_budget_log(node_id, provider, recorded_at DESC);
		CREATE TABLE IF NOT EXISTS token_budget_snapshots (
			node_id              TEXT NOT NULL,
			provider             TEXT NOT NULL,
			monthly_pct          INTEGER NOT NULL DEFAULT 0,
			weekly_pct           INTEGER NOT NULL DEFAULT 0,
			h5_pct               INTEGER NOT NULL DEFAULT 0,
			status               TEXT NOT NULL DEFAULT 'OK',
			cooldown_until       TEXT,
			agents               TEXT,
			snapshot_age_seconds INTEGER NOT NULL DEFAULT 0,
			last_reconciled_at   TEXT NOT NULL DEFAULT (datetime('now')),
			PRIMARY KEY (node_id, provider)
		);
		CREATE INDEX IF NOT EXISTS idx_token_budget_snapshots_status
			ON token_budget_snapshots(status);
	`)
	return err
}

// computeBudgetStatus derives OK/CAUTION/RED/DEPLETED/COOLDOWN from usage pcts.
// Mirrors the same logic in the CLI (workflow_fleet.go).
func computeBudgetStatus(monthly, weekly, h5 int, cooldownUntil *string) string {
	if cooldownUntil != nil && *cooldownUntil != "" {
		if t, err := time.Parse(time.RFC3339, *cooldownUntil); err == nil {
			if time.Until(t) > 0 {
				return "COOLDOWN"
			}
		}
	}
	worst := monthly
	if weekly > worst {
		worst = weekly
	}
	if h5 > worst {
		worst = h5
	}
	switch {
	case worst >= 95:
		return "DEPLETED"
	case worst >= 90:
		return "RED"
	case worst >= 80:
		return "CAUTION"
	default:
		return "OK"
	}
}

// tokenBudgetCooldownResetPatrol is the ADR-035 Phase 2 token budget patrol.
// Every 5 minutes it:
//  1. Reads the local per-node JSON budget file.
//  2. Clears any cooldowns whose cooldown_until timestamp has passed.
//  3. Upserts the token_budget_snapshots table for cross-node API reads.
//  4. Appends an audit row to token_budget_log for status changes.
func tokenBudgetCooldownResetPatrol(ctx context.Context, db *sql.DB) error {
	if err := ensureTokenBudgetTables(ctx, db); err != nil {
		log.Printf("[Patrol:token-budget] ensure tables error: %v", err)
		// non-fatal — continue so snapshots still get tried
	}

	nodeID := nodeIDFromEnv()
	path := tokenBudgetFilePath(".", nodeID)

	budget, err := readTokenBudgetFile(path)
	if err != nil {
		return fmt.Errorf("token-budget patrol: %w", err)
	}
	if budget == nil {
		// File not seeded yet — nothing to do.
		log.Printf("[Patrol:token-budget] budget file not found (%s), skipping", path)
		return nil
	}

	now := time.Now().UTC()
	fileModified := false
	var logEntries []tbLogEntry

	for name, p := range budget.Providers {
		oldStatus := p.Status

		// 1. Clear expired cooldowns.
		if p.CooldownUntil != nil && *p.CooldownUntil != "" {
			if t, err := time.Parse(time.RFC3339, *p.CooldownUntil); err == nil {
				if now.After(t) {
					log.Printf("[Patrol:token-budget] provider %s cooldown expired — clearing", name)
					p.CooldownUntil = nil
					fileModified = true
				}
			}
		}

		// 2. Re-derive status from current limits.
		monthly := p.Limits["monthly_pct"]
		weekly := p.Limits["weekly_pct"]
		h5 := p.Limits["5h_rolling_pct"]
		newStatus := computeBudgetStatus(monthly, weekly, h5, p.CooldownUntil)
		if newStatus != p.Status {
			log.Printf("[Patrol:token-budget] provider %s status: %s → %s", name, p.Status, newStatus)
			p.Status = newStatus
			fileModified = true
		}

		budget.Providers[name] = p

		// 3. Queue snapshot upsert + log entry if status changed.
		logEntries = append(logEntries, tbLogEntry{
			nodeID:        nodeID,
			provider:      name,
			eventType:     eventTypeFor(oldStatus, newStatus),
			monthlyPct:    monthly,
			weeklyPct:     weekly,
			h5Pct:         h5,
			status:        newStatus,
			cooldownUntil: p.CooldownUntil,
			agents:        p.Agents,
		})
	}

	// 4. Write back JSON file if anything changed.
	if fileModified {
		if err := writeTokenBudgetFile(path, budget); err != nil {
			log.Printf("[Patrol:token-budget] write error: %v", err)
		}
	}

	// 5. Upsert snapshots + log changes into SQLite.
	for _, entry := range logEntries {
		if err := upsertTokenBudgetSnapshot(ctx, db, entry, now); err != nil {
			log.Printf("[Patrol:token-budget] snapshot upsert error (provider=%s): %v", entry.provider, err)
		}
		if entry.eventType != "no_change" {
			if err := insertTokenBudgetLog(ctx, db, entry); err != nil {
				log.Printf("[Patrol:token-budget] log insert error (provider=%s): %v", entry.provider, err)
			}
		}
	}

	log.Printf("[Patrol:token-budget] synced %d providers from %s", len(logEntries), path)
	return nil
}

type tbLogEntry struct {
	nodeID        string
	provider      string
	eventType     string
	monthlyPct    int
	weeklyPct     int
	h5Pct         int
	status        string
	cooldownUntil *string
	agents        []string
}

func eventTypeFor(oldStatus, newStatus string) string {
	if oldStatus == newStatus {
		return "no_change"
	}
	switch {
	case newStatus == "COOLDOWN":
		return "cooldown_set"
	case oldStatus == "COOLDOWN" && newStatus != "COOLDOWN":
		return "cooldown_clear"
	case newStatus == "OK" && oldStatus != "OK":
		return "reset"
	default:
		return "update"
	}
}

func upsertTokenBudgetSnapshot(ctx context.Context, db *sql.DB, e tbLogEntry, now time.Time) error {
	agentsJSON, _ := json.Marshal(e.agents)
	cooldown := ""
	if e.cooldownUntil != nil {
		cooldown = *e.cooldownUntil
	}

	_, err := db.ExecContext(ctx, `
		INSERT INTO token_budget_snapshots
			(node_id, provider, monthly_pct, weekly_pct, h5_pct, status, cooldown_until,
			 agents, snapshot_age_seconds, last_reconciled_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
		ON CONFLICT(node_id, provider) DO UPDATE SET
			monthly_pct          = excluded.monthly_pct,
			weekly_pct           = excluded.weekly_pct,
			h5_pct               = excluded.h5_pct,
			status               = excluded.status,
			cooldown_until       = excluded.cooldown_until,
			agents               = excluded.agents,
			snapshot_age_seconds = 0,
			last_reconciled_at   = excluded.last_reconciled_at
	`, e.nodeID, e.provider, e.monthlyPct, e.weeklyPct, e.h5Pct,
		e.status, cooldown, string(agentsJSON), now.UTC().Format(time.RFC3339))
	return err
}

func insertTokenBudgetLog(ctx context.Context, db *sql.DB, e tbLogEntry) error {
	id := fmt.Sprintf("tbl-%s-%s-%d", e.nodeID, e.provider, time.Now().UnixNano())
	cooldown := ""
	if e.cooldownUntil != nil {
		cooldown = *e.cooldownUntil
	}
	_, err := db.ExecContext(ctx, `
		INSERT INTO token_budget_log
			(id, node_id, provider, event_type, monthly_pct, weekly_pct, h5_pct,
			 status, cooldown_until, source, recorded_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'patrol', datetime('now'))
	`, id, e.nodeID, e.provider, e.eventType,
		e.monthlyPct, e.weeklyPct, e.h5Pct,
		e.status, cooldown)
	return err
}

// nodeIDFromEnv returns the node identity: NODE_ID env var, else hostname.
func nodeIDFromEnv() string {
	if id := os.Getenv("NODE_ID"); id != "" {
		return id
	}
	h, err := os.Hostname()
	if err != nil {
		return "unknown"
	}
	// Strip domain suffix if present (e.g. "prya.local" → "prya")
	return strings.SplitN(h, ".", 2)[0]
}
