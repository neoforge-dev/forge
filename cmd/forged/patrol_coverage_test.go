package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRequeueRetriableTasks_Coverage(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	
	// Create a task that should be requeued
	// retry_count < 3, updated_at < 60s ago
	oldTime := time.Now().Add(-2 * time.Minute).Format("2006-01-02 15:04:05")
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, status, state, retry_count, updated_at, title)
		VALUES ('task-requeue-cov', 'd', 'p', 't', 'failed', 'FAILED', 1, ?, 'test')
	`, oldTime)
	require.NoError(t, err)

	err = requeueRetriableTasks(context.Background(), db)
	assert.NoError(t, err)

	var status, state string
	err = db.QueryRow("SELECT status, state FROM tasks WHERE id='task-requeue-cov'").Scan(&status, &state)
	assert.NoError(t, err)
	assert.Equal(t, "queued", status)
	assert.Equal(t, "QUEUED", state)
}

func TestHandleTimedOutTasks_Coverage(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	
	// Task executing for > 30 minutes
	oldTime := time.Now().Add(-40 * time.Minute).Format("2006-01-02 15:04:05")
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, status, started_at, title)
		VALUES ('task-timeout-cov', 'd', 'p', 't', 'executing', ?, 'test')
	`, oldTime)
	require.NoError(t, err)

	err = handleTimedOutTasks(context.Background(), db)
	assert.NoError(t, err)

	var status string
	err = db.QueryRow("SELECT status FROM tasks WHERE id='task-timeout-cov'").Scan(&status)
	assert.NoError(t, err)
	assert.Equal(t, "queued", status)
}

func TestExpireOldApprovals_Coverage(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	
	// Pending approval that expired
	oldTime := time.Now().Add(-1 * time.Hour).Format("2006-01-02 15:04:05")
	_, err := db.Exec(`
		INSERT INTO approvals (id, type, agent_id, domain, title, status, expires_at)
		VALUES ('app-expired-cov', 'task_completion', 'agent1', 'test-domain', 'Test Approval', 'pending', ?)
	`, oldTime)
	require.NoError(t, err)

	err = expireOldApprovals(context.Background(), db)
	assert.NoError(t, err)

	var status string
	err = db.QueryRow("SELECT status FROM approvals WHERE id='app-expired-cov'").Scan(&status)
	assert.NoError(t, err)
	assert.Equal(t, "expired", status)
}

func TestMonitorQueueDepth_Coverage(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	
	// Add some queued tasks
	for i := 0; i < 10; i++ {
		_, _ = db.Exec("INSERT INTO tasks (id, domain, project, type, status, title) VALUES (?, 'd', 'p', 't', 'queued', 't')", i)
	}

	// Just ensure it doesn't crash
	err := monitorQueueDepth(context.Background(), db)
	assert.NoError(t, err)
}

func TestDataRetentionPatrol_Coverage(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	
	// Stale queued task (7 days old)
	oldTime := time.Now().Add(-8 * 24 * time.Hour).Format("2006-01-02 15:04:05")
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, status, created_at, title, lane)
		VALUES ('task-stale-cov', 'd', 'p', 't', 'queued', ?, 'test', NULL)
	`, oldTime)
	require.NoError(t, err)

	err = dataRetentionPatrol(context.Background(), db)
	assert.NoError(t, err)

	var status string
	err = db.QueryRow("SELECT status FROM tasks WHERE id='task-stale-cov'").Scan(&status)
	assert.NoError(t, err)
	assert.Equal(t, "abandoned", status)
}

func TestDailyDigestPatrol_Coverage(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	tmpDir := t.TempDir()
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	err := dailyDigestPatrol(context.Background(), db)
	assert.NoError(t, err)

	// Verify file was created
	today := time.Now().UTC().Format("2006-01-02")
	digestPath := filepath.Join(tmpDir, ".forge/heartbeat/results", "daily-digest-"+today+".md")
	_, err = os.Stat(digestPath)
	assert.NoError(t, err)
}
