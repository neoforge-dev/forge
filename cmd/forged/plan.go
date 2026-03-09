//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	"github.com/oklog/ulid/v2"
)

type PlanVersion struct {
	ID        string    `json:"id"`
	TaskID    string    `json:"task_id"`
	Version   int       `json:"version"`
	Plan      string    `json:"plan"`
	Reason    string    `json:"reason,omitempty"`
	CreatedAt time.Time `json:"created_at"`
}

type PlanEvent struct {
	ID        int       `json:"id"`
	PlanID    string    `json:"plan_id"`
	EventType string    `json:"event_type"`
	Payload   string    `json:"payload,omitempty"`
	CreatedAt time.Time `json:"created_at"`
}

type PlanManager interface {
	CreatePlan(ctx context.Context, taskID, plan, reason string) (string, error)
	RevisePlan(ctx context.Context, taskID, newPlan, reason string) (string, error)
	GetPlanHistory(ctx context.Context, taskID string) ([]PlanVersion, error)
}

type sqlitePlanManager struct {
	db *sql.DB
}

func NewPlanManager(db *sql.DB) PlanManager {
	return &sqlitePlanManager{db: db}
}

func (m *sqlitePlanManager) CreatePlan(ctx context.Context, taskID, plan, reason string) (string, error) {
	planID := ulid.Make().String()
	version := 1
	now := time.Now()

	tx, err := m.db.BeginTx(ctx, nil)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()

	// Insert into plan_versions
	_, err = tx.ExecContext(ctx,
		"INSERT INTO plan_versions (id, task_id, version, plan, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
		planID, taskID, version, plan, reason, now.Format(time.RFC3339))
	if err != nil {
		return "", fmt.Errorf("failed to insert plan version: %w", err)
	}

	// Update task with plan info and status PLANNED
	_, err = tx.ExecContext(ctx,
		"UPDATE tasks SET plan_id = ?, plan_version = ?, status = ?, updated_at = ? WHERE id = ?",
		planID, version, TaskStatusPlanned, now.Format(time.RFC3339), taskID)
	if err != nil {
		return "", fmt.Errorf("failed to update task: %w", err)
	}

	// Record event
	_, err = tx.ExecContext(ctx,
		"INSERT INTO plan_events (plan_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
		planID, "plan.created", reason, now.Format(time.RFC3339))
	if err != nil {
		return "", fmt.Errorf("failed to insert plan event: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return "", fmt.Errorf("failed to commit transaction: %w", err)
	}

	return planID, nil
}

func (m *sqlitePlanManager) RevisePlan(ctx context.Context, taskID, newPlan, reason string) (string, error) {
	// Get current version
	var currentVersion int
	err := m.db.QueryRowContext(ctx, "SELECT plan_version FROM tasks WHERE id = ?", taskID).Scan(&currentVersion)
	if err != nil {
		return "", fmt.Errorf("failed to get current plan version: %w", err)
	}

	planID := ulid.Make().String()
	newVersion := currentVersion + 1
	now := time.Now()

	tx, err := m.db.BeginTx(ctx, nil)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()

	// Insert into plan_versions
	_, err = tx.ExecContext(ctx,
		"INSERT INTO plan_versions (id, task_id, version, plan, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
		planID, taskID, newVersion, newPlan, reason, now.Format(time.RFC3339))
	if err != nil {
		return "", fmt.Errorf("failed to insert plan version: %w", err)
	}

	// Update task
	_, err = tx.ExecContext(ctx,
		"UPDATE tasks SET plan_id = ?, plan_version = ?, updated_at = ? WHERE id = ?",
		planID, newVersion, now.Format(time.RFC3339), taskID)
	if err != nil {
		return "", fmt.Errorf("failed to update task: %w", err)
	}

	// Record event
	_, err = tx.ExecContext(ctx,
		"INSERT INTO plan_events (plan_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
		planID, "plan.revised", reason, now.Format(time.RFC3339))
	if err != nil {
		return "", fmt.Errorf("failed to insert plan event: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return "", fmt.Errorf("failed to commit transaction: %w", err)
	}

	return planID, nil
}

func (m *sqlitePlanManager) GetPlanHistory(ctx context.Context, taskID string) ([]PlanVersion, error) {
	rows, err := m.db.QueryContext(ctx,
		"SELECT id, task_id, version, plan, reason, created_at FROM plan_versions WHERE task_id = ? ORDER BY version ASC",
		taskID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var history []PlanVersion
	for rows.Next() {
		var pv PlanVersion
		var createdAt string
		if err := rows.Scan(&pv.ID, &pv.TaskID, &pv.Version, &pv.Plan, &pv.Reason, &createdAt); err != nil {
			return nil, err
		}
		pv.CreatedAt, _ = time.Parse(time.RFC3339, createdAt)
		history = append(history, pv)
	}
	return history, nil
}
