package main

import (
	"context"
	"database/sql"
	"os"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func setupLeaseTestDB(t *testing.T) *sql.DB {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}

	schema := `
	CREATE TABLE IF NOT EXISTS leases (
		id TEXT PRIMARY KEY,
		task_id TEXT NOT NULL UNIQUE,
		agent_id TEXT NOT NULL,
		expires_at TEXT NOT NULL
	);
	CREATE INDEX IF NOT EXISTS idx_leases_expires ON leases (expires_at);

	CREATE TABLE IF NOT EXISTS tasks (
		id TEXT PRIMARY KEY,
		domain TEXT,
		project TEXT,
		type TEXT,
		priority INTEGER,
		status TEXT,
		state TEXT DEFAULT 'QUEUED',
		lane TEXT,
		assigned_to TEXT,
		plan_version INTEGER,
		plan_id TEXT,
		envelope_id TEXT,
		created_at TEXT,
		updated_at TEXT
	);

	CREATE TABLE IF NOT EXISTS task_state_transitions (
		id TEXT PRIMARY KEY,
		task_id TEXT NOT NULL,
		from_state TEXT,
		to_state TEXT,
		reason TEXT,
		transitioned_at TEXT
	);
	`
	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("failed to create schema: %v", err)
	}

	return db
}

func TestClaim(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim failed: %v", err)
	}
	if lease.TaskID != "task-1" {
		t.Errorf("expected task-1, got %s", lease.TaskID)
	}
	if lease.AgentID != "agent-1" {
		t.Errorf("expected agent-1, got %s", lease.AgentID)
	}
	if lease.ID == "" {
		t.Error("lease ID should not be empty")
	}
}

func TestClaimConflict(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	_, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
	if err != nil {
		t.Fatalf("first Claim failed: %v", err)
	}

	_, err = m.Claim(ctx, "task-1", "agent-2", 30*time.Minute)
	if err != ErrLeaseConflict {
		t.Errorf("expected ErrLeaseConflict, got %v", err)
	}
}

func TestRenew(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim failed: %v", err)
	}

	newTTL := 60 * time.Minute
	err = m.Renew(ctx, lease.ID, newTTL)
	if err != nil {
		t.Fatalf("Renew failed: %v", err)
	}

	var expiresAt string
	err = db.QueryRow("SELECT expires_at FROM leases WHERE id = ?", lease.ID).Scan(&expiresAt)
	if err != nil {
		t.Fatalf("failed to get expires_at: %v", err)
	}

	parsed, _ := time.Parse(time.RFC3339, expiresAt)
	if parsed.Sub(time.Now()) < 45*time.Minute {
		t.Error("lease should be renewed for ~60 minutes")
	}
}

func TestRelease(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim failed: %v", err)
	}

	err = m.Release(ctx, lease.ID)
	if err != nil {
		t.Fatalf("Release failed: %v", err)
	}

	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM leases WHERE id = ?", lease.ID).Scan(&count)
	if err != nil {
		t.Fatalf("failed to query: %v", err)
	}
	if count != 0 {
		t.Error("lease should be released")
	}
}

func TestRecover(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	_, err := m.Claim(ctx, "task-1", "agent-1", -1*time.Hour)
	if err != nil {
		t.Fatalf("Claim failed: %v", err)
	}

	recovered, err := m.Recover(ctx)
	if err != nil {
		t.Fatalf("Recover failed: %v", err)
	}
	if len(recovered) == 0 {
		t.Fatal("expected 1 recovered lease, got 0")
	}
	if recovered[0].TaskID != "task-1" {
		t.Errorf("expected task-1, got %s", recovered[0].TaskID)
	}

	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM leases").Scan(&count)
	if err != nil {
		t.Fatalf("failed to query: %v", err)
	}
	if count != 0 {
		t.Error("stale leases should be deleted after recover")
	}
}

func TestClaimAfterRelease(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim failed: %v", err)
	}

	err = m.Release(ctx, lease.ID)
	if err != nil {
		t.Fatalf("Release failed: %v", err)
	}

	lease2, err := m.Claim(ctx, "task-1", "agent-2", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim after release failed: %v", err)
	}
	if lease2.AgentID != "agent-2" {
		t.Errorf("expected agent-2, got %s", lease2.AgentID)
	}
}

func TestRenewNonExistent(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	err := m.Renew(ctx, "non-existent", 30*time.Minute)
	if err != ErrLeaseNotFound {
		t.Errorf("expected ErrLeaseNotFound, got %v", err)
	}
}

func TestReleaseNonExistent(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	err := m.Release(ctx, "non-existent")
	if err != ErrLeaseNotFound {
		t.Errorf("expected ErrLeaseNotFound, got %v", err)
	}
}

// TestLeaseStateMachine tests the integration with task state machine
func TestLeaseStateMachine(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	// Insert a test task
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at) 
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"task-1", "test-domain", "test-project", "feature", 1, "queued", StateQueued,
		time.Now().Format(time.RFC3339), time.Now().Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert test task: %v", err)
	}

	// Create lease manager with state machine
	sm := NewTaskStateMachine(db)
	m := &sqliteLeaseManager{db: db, stateMachine: sm}
	ctx := context.Background()

	t.Run("AcquireLease transitions to DISPATCHED", func(t *testing.T) {
		lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
		if err != nil {
			t.Fatalf("Claim failed: %v", err)
		}

		// Verify state transition
		state, err := sm.GetTaskState("task-1")
		if err != nil {
			t.Fatalf("Failed to get task state: %v", err)
		}
		if state != StateDispatched {
			t.Errorf("expected state %s, got %s", StateDispatched, state)
		}

		// Clean up
		m.Release(ctx, lease.ID)
	})

	t.Run("Heartbeat transitions DISPATCHED to RUNNING", func(t *testing.T) {
		// Reset task state
		db.Exec("UPDATE tasks SET state = ? WHERE id = ?", StateQueued, "task-1")

		lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
		if err != nil {
			t.Fatalf("Claim failed: %v", err)
		}

		// Record heartbeat
		err = m.RecordHeartbeat(ctx, lease.ID)
		if err != nil {
			t.Fatalf("RecordHeartbeat failed: %v", err)
		}

		// Verify state transition to RUNNING
		state, err := sm.GetTaskState("task-1")
		if err != nil {
			t.Fatalf("Failed to get task state: %v", err)
		}
		if state != StateRunning {
			t.Errorf("expected state %s, got %s", StateRunning, state)
		}

		// Clean up
		m.Release(ctx, lease.ID)
	})

	t.Run("Release transitions to COMPLETED", func(t *testing.T) {
		// Reset task state
		db.Exec("UPDATE tasks SET state = ? WHERE id = ?", StateQueued, "task-1")

		lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
		if err != nil {
			t.Fatalf("Claim failed: %v", err)
		}

		// Release lease
		err = m.Release(ctx, lease.ID)
		if err != nil {
			t.Fatalf("Release failed: %v", err)
		}

		// Verify state transition to COMPLETED
		state, err := sm.GetTaskState("task-1")
		if err != nil {
			t.Fatalf("Failed to get task state: %v", err)
		}
		if state != StateCompleted {
			t.Errorf("expected state %s, got %s", StateCompleted, state)
		}
	})

	t.Run("Expired lease transitions to FAILED", func(t *testing.T) {
		// Reset task state
		db.Exec("UPDATE tasks SET state = ? WHERE id = ?", StateQueued, "task-1")

		// Create expired lease
		_, err := m.Claim(ctx, "task-1", "agent-1", -1*time.Hour)
		if err != nil {
			t.Fatalf("Claim failed: %v", err)
		}

		// Recover (this deletes expired leases and transitions tasks)
		_, err = m.Recover(ctx)
		if err != nil {
			t.Fatalf("Recover failed: %v", err)
		}

		// Verify state transition to FAILED
		state, err := sm.GetTaskState("task-1")
		if err != nil {
			t.Fatalf("Failed to get task state: %v", err)
		}
		if state != StateFailed {
			t.Errorf("expected state %s, got %s", StateFailed, state)
		}
	})
}

func TestGetTaskForLease(t *testing.T) {
	db := setupLeaseTestDB(t)
	defer db.Close()

	// Insert a test task
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at) 
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"task-1", "test-domain", "test-project", "feature", 1, "queued", StateQueued,
		time.Now().Format(time.RFC3339), time.Now().Format(time.RFC3339))
	if err != nil {
		t.Fatalf("failed to insert test task: %v", err)
	}

	m := &sqliteLeaseManager{db: db}
	ctx := context.Background()

	lease, err := m.Claim(ctx, "task-1", "agent-1", 30*time.Minute)
	if err != nil {
		t.Fatalf("Claim failed: %v", err)
	}

	task, err := m.GetTaskForLease(ctx, lease.ID)
	if err != nil {
		t.Fatalf("GetTaskForLease failed: %v", err)
	}

	if task.ID != "task-1" {
		t.Errorf("expected task-1, got %s", task.ID)
	}
	if task.Domain != "test-domain" {
		t.Errorf("expected test-domain, got %s", task.Domain)
	}
}

func TestMain(m *testing.M) {
	// Run tests
	code := m.Run()

	// Cleanup goroutines started during tests
	StopXNode()
	StopMetrics()

	os.Exit(code)
}
