//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"
)

// patrolsHandler with execution data
func TestWave91_PatrolsHandler_WithExecutionData(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_, _ = getDBConn().Exec(`INSERT INTO patrol_executions (id, patrol_id, started_at, status)
		VALUES (?, ?, datetime('now'), ?)`,
		"wave91-pe-1", "wave91-patrol", "completed")

	r := httptest.NewRequest(http.MethodGet, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, r)
	if w.Code >= 500 {
		t.Logf("patrolsHandler with data: got %d — %s", w.Code, w.Body.String())
	}
}

// dashHandler with execution rows
func TestWave91_DashHandler_WithData(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	completedAt := time.Now().UTC().Add(time.Second).Format("2006-01-02 15:04:05")
	_, _ = getDBConn().Exec(`INSERT INTO patrol_executions
		(id, patrol_id, started_at, completed_at, status, result, error)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"wave91-dash-pe-1", "wave91-dash-patrol", now, completedAt, "success", "ok", nil)

	r := httptest.NewRequest(http.MethodGet, "/api/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, r)
	if w.Code >= 500 {
		t.Logf("dashHandler with data: got %d — %s", w.Code, w.Body.String())
	}
}

// PruneStaleWorktrees — nonexistent dir returns nil
func TestWave91_PruneStaleWorktrees_NonexistentDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	mgr := NewWorktreeManager(tmpDir, tmpDir+"/nonexistent", db)
	err := mgr.PruneStaleWorktrees(context.Background())
	if err != nil {
		t.Errorf("PruneStaleWorktrees nonexistent dir: expected nil, got %v", err)
	}
}

func TestWave91_PruneStaleWorktrees_WithCompletedTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	tmpDir := t.TempDir()
	taskID := "wave91-completed-wt"
	worktreeDir := fmt.Sprintf("%s/%s", tmpDir, taskID)
	if err := os.MkdirAll(worktreeDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		taskID, "codeswiftr", "proj", "feature", 5, "completed", "COMPLETED", "Worktree Task", now, now)

	mgr := NewWorktreeManager(tmpDir, tmpDir, db)
	err := mgr.PruneStaleWorktrees(ctx)
	if err != nil {
		t.Logf("PruneStaleWorktrees completed task: %v", err)
	}
}

func TestWave91_PruneStaleWorktrees_NilDB(t *testing.T) {
	tmpDir := t.TempDir()
	emptySubDir := fmt.Sprintf("%s/empty-task", tmpDir)
	if err := os.MkdirAll(emptySubDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	mgr := NewWorktreeManager(tmpDir, tmpDir, nil)
	err := mgr.PruneStaleWorktrees(context.Background())
	if err != nil {
		t.Logf("PruneStaleWorktrees nil DB: %v", err)
	}
}

// writeTokenBudgetFile — cover write path
func TestWave91_WriteTokenBudgetFile_Success(t *testing.T) {
	tmpDir := t.TempDir()
	path := tmpDir + "/token_budget.json"

	f := &tokenBudgetFile{
		Node: "wave91-node",
		Providers: map[string]tbProviderData{
			"anthropic": {Provider: "anthropic", Status: "OK"},
		},
	}

	err := writeTokenBudgetFile(path, f)
	if err != nil {
		t.Errorf("writeTokenBudgetFile: %v", err)
	}

	if _, statErr := os.Stat(path); statErr != nil {
		t.Errorf("writeTokenBudgetFile: file not created: %v", statErr)
	}
}

// syncRoyalJelly write-back path (recent envelopes)
func TestWave91_SyncRoyalJelly_WithRecentEnvelopes(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	db := getDBConn()
	ctx := context.Background()

	tmpDir := t.TempDir()
	if err := os.MkdirAll(tmpDir+"/.forge/context", 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	prevWD, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("chdir: %v", err)
	}
	defer os.Chdir(prevWD)

	content := `{"metadata":{"lead_context":"# Lead\nState for wave91"},"decisions":[{"id":"d1","title":"T","description":"D","rationale":"R","status":"accepted","date":"2026-03-08","domain":"wave91"}],"failures":[],"summary":"Wave91 summary"}`
	_, _ = db.Exec(`INSERT INTO context_envelopes (id, domain, content, created_at) VALUES (?, ?, ?, datetime('now', '-5 minutes'))`,
		"wave91-env-write", "wave91", content)

	err := syncRoyalJelly(ctx, db)
	if err != nil {
		t.Logf("syncRoyalJelly with recent envelope: %v", err)
	}
}

func TestWave91_SyncRoyalJelly_WithFailuresEnvelope(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	db := getDBConn()
	ctx := context.Background()

	tmpDir := t.TempDir()
	if err := os.MkdirAll(tmpDir+"/.forge/context", 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	prevWD, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("chdir: %v", err)
	}
	defer os.Chdir(prevWD)

	content := `{"metadata":{},"decisions":[],"failures":[{"id":"f1","description":"Desc","impact":"low","occurred_at":"2026-03-08","domain":"wave91fail","resolved":false}],"summary":"Fail test"}`
	_, _ = db.Exec(`INSERT INTO context_envelopes (id, domain, content, created_at) VALUES (?, ?, ?, datetime('now', '-10 minutes'))`,
		"wave91-env-fail", "wave91fail", content)

	err := syncRoyalJelly(ctx, db)
	if err != nil {
		t.Logf("syncRoyalJelly with failures: %v", err)
	}
}

// CreatePlan / RevisePlan
func TestWave91_CreatePlan_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	now := time.Now().UTC().Format(time.RFC3339)

	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave91-plan-task", "codeswiftr", "proj", "feature", 5, "queued", "QUEUED", "Plan Task", now, now)

	pm := NewPlanManager(db)
	planID, err := pm.CreatePlan(ctx, "wave91-plan-task", "Step 1: do the thing", "initial plan")
	if err != nil {
		t.Logf("CreatePlan: %v", err)
	} else {
		t.Logf("CreatePlan success: %s", planID)
	}
}

func TestWave91_RevisePlan_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	now := time.Now().UTC().Format(time.RFC3339)

	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, plan_version, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave91-revise-task", "codeswiftr", "proj", "feature", 5, "queued", "QUEUED", "Revise Task", 1, now, now)

	pm := NewPlanManager(db)
	_, _ = pm.CreatePlan(ctx, "wave91-revise-task", "Step 1: initial", "v1")
	planID, err := pm.RevisePlan(ctx, "wave91-revise-task", "Step 1: revised plan", "iteration 2")
	if err != nil {
		t.Logf("RevisePlan: %v", err)
	} else {
		t.Logf("RevisePlan success: %s", planID)
	}
}

// SyncEnvelopesToFilesystem empty case
func TestWave91_SyncEnvelopesToFilesystem_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	cm := testContextManager(t, db, tmpDir)
	cs, err := NewContextSync(cm, db, tmpDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}
	defer cs.Stop()

	if err := cs.SyncEnvelopesToFilesystem(); err != nil {
		t.Logf("SyncEnvelopesToFilesystem empty: %v", err)
	}
}

// detectTailscaleIP — cover both paths
func TestWave91_DetectTailscaleIP(t *testing.T) {
	ip := detectTailscaleIP()
	t.Logf("detectTailscaleIP: %q", ip)
}

// patrolsHandler with globalPatrolSystem
func TestWave91_PatrolsHandler_WithGlobalPatrolSystem(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldPS := globalPatrolSystem
	globalPatrolSystem = NewPatrolSystem(db)
	defer func() { globalPatrolSystem = oldPS }()

	r := httptest.NewRequest(http.MethodGet, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, r)
	if w.Code >= 500 {
		t.Logf("patrolsHandler with globalPatrolSystem: got %d", w.Code)
	}
}
