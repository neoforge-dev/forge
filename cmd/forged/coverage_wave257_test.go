//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"errors"
	"net/http"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func TestWave257_StandardPatrols(t *testing.T) {
	patrols := StandardPatrols()
	if len(patrols) < 20 {
		t.Errorf("expected at least 20 standard patrols, got %d", len(patrols))
	}
}

func TestWave257_PatrolSystem_Lifecycle(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)
	
	// Create a dummy patrol with short schedule
	dummyPatrol := Patrol{
		ID:       "dummy-patrol",
		Name:     "Dummy",
		Schedule: 10 * time.Millisecond,
		Action: func(ctx context.Context, db *sql.DB) error {
			return nil
		},
	}
	
	// We replace standard patrols to avoid side effects during lifecycle test
	ps.patrols = []Patrol{dummyPatrol}
	
	ps.Start()
	time.Sleep(50 * time.Millisecond)
	ps.Stop()
}

func TestWave257_PatrolSystem_AddContextPatrol(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)
	cp := ContextAwarePatrol{
		Patrol: Patrol{ID: "ctx-patrol", Name: "Ctx"},
		cm:     nil,
	}
	ps.AddContextPatrol(cp)
	
	found := false
	for _, p := range ps.contextPatrols {
		if p.ID == "ctx-patrol" {
			found = true
			break
		}
	}
	if !found {
		t.Error("context patrol not added")
	}
}

func TestWave257_RunPatrolByID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)
	
	runCount := 0
	p := Patrol{
		ID: "test-id",
		Action: func(ctx context.Context, db *sql.DB) error {
			runCount++
			return nil
		},
	}
	ps.patrols = []Patrol{p}
	
	// 1. Success
	if !ps.RunPatrolByID("test-id") {
		t.Error("expected true for existing patrol ID")
	}
	if runCount != 1 {
		t.Errorf("expected 1 run, got %d", runCount)
	}
	
	// 2. Not found
	if ps.RunPatrolByID("non-existent") {
		t.Error("expected false for non-existent ID")
	}
	
	// 3. Context patrol success
	ctxRunCount := 0
	cp := ContextAwarePatrol{
		Patrol: Patrol{
			ID: "ctx-id",
			Action: func(ctx context.Context, db *sql.DB) error {
				ctxRunCount++
				return nil
			},
		},
	}
	ps.contextPatrols = []ContextAwarePatrol{cp}
	if !ps.RunPatrolByID("ctx-id") {
		t.Error("expected true for existing context patrol ID")
	}
	if ctxRunCount != 1 {
		t.Errorf("expected 1 context run, got %d", ctxRunCount)
	}
}

func TestWave257_ExecutePatrol_ErrorPath(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)
	
	p := Patrol{
		ID: "error-patrol",
		Action: func(ctx context.Context, db *sql.DB) error {
			return errors.New("boom")
		},
	}
	
	// This should log and record error but not panic
	ps.executePatrol(p)
	
	// Verify error was recorded in DB
	var count int
	err := db.QueryRow("SELECT COUNT(*) FROM patrol_executions WHERE patrol_id = ? AND status = 'error'", "error-patrol").Scan(&count)
	if err != nil {
		t.Fatalf("failed to query patrol_executions: %v", err)
	}
	if count == 0 {
		t.Error("expected error to be recorded in DB")
	}
}

func TestWave257_ExecuteContextPatrol_ErrorPaths(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ps := NewPatrolSystem(db)
	
	// 1. Action error
	cp := ContextAwarePatrol{
		Patrol: Patrol{
			ID: "ctx-error",
			Action: func(ctx context.Context, db *sql.DB) error {
				return errors.New("action boom")
			},
		},
	}
	ps.executeContextPatrol(cp)
	
	// 2. checkContextThreshold error (needs cm)
	// We can't easily trigger error inside checkContextThreshold without complex setup, 
	// but we can pass nil to some parts if they are not guarded.
	// Actually, ps.executeContextPatrol calls checkContextThreshold(ctx, ps.db, patrol.cm, ps.rj)
	// If cp.cm is NOT nil, it proceeds.
}

func TestWave257_AutoPromote_Gaps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert a COMPLETED task
	_, err := db.Exec(`INSERT INTO tasks (id, title, domain, project, type, lane, status, state, updated_at) 
		VALUES ('task-promote', 'Task', 'test', 'test', 'feature', 'dev', 'completed', 'COMPLETED', datetime('now', '-2 minutes'))`)
	if err != nil {
		t.Fatal(err)
	}


	// 1. globalLaneManager == nil
	oldLM := globalLaneManager
	globalLaneManager = nil
	defer func() { globalLaneManager = oldLM }()
	
	if err := autoPromoteCompletedTasksInLane(context.Background(), db); err != nil {
		t.Errorf("expected no error when globalLaneManager is nil, got %v", err)
	}
	
	// 2. ProgressTask error
	// Mock lane manager
	globalLaneManager = &mockLaneManager{err: errors.New("progress failed")}
	if err := autoPromoteCompletedTasksInLane(context.Background(), db); err != nil {
		t.Errorf("expected no error returned (just logged) on progress failure, got %v", err)
	}
}

type mockLaneManager struct {
	err error
}

func (m *mockLaneManager) ProgressTask(ctx context.Context, id string) (*Task, error) {
	return nil, m.err
}

func (m *mockLaneManager) CheckGates(ctx context.Context, task *Task, targetLane Lane) (*GateResult, error) {
	return nil, nil
}

func (m *mockLaneManager) HandleLaneComplete(w http.ResponseWriter, r *http.Request) {}
func (m *mockLaneManager) HandleLaneStatus(w http.ResponseWriter, r *http.Request)   {}
