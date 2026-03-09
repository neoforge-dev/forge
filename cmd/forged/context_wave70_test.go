//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// wave70ContextDB creates a context_envelopes table in-memory.
func wave70ContextDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("wave70: open db: %v", err)
	}
	_, err = db.Exec(`
		CREATE TABLE context_envelopes (
			id         TEXT PRIMARY KEY,
			agent_id   TEXT NOT NULL,
			domain     TEXT NOT NULL,
			project    TEXT NOT NULL DEFAULT '',
			task_id    TEXT NOT NULL DEFAULT '',
			summary    TEXT,
			content    TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			expires_at TIMESTAMP NOT NULL DEFAULT (datetime('now','+1 day'))
		)
	`)
	if err != nil {
		t.Fatalf("wave70: create table: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

// wave70CM builds a ContextManager pointing at a temp contextDir.
func wave70CM(t *testing.T, db *sql.DB) (*ContextManager, string) {
	t.Helper()
	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	if err := os.MkdirAll(contextDir, 0755); err != nil {
		t.Fatalf("wave70: mkdir: %v", err)
	}
	// Use a plain ContextManager (no live sync goroutine) so tests are deterministic.
	cm := &ContextManager{db: db, contextDir: contextDir}
	return cm, contextDir
}

// ---------------------------------------------------------------------------
// context.go: GenerateEnvelope — cover extra branches
// ---------------------------------------------------------------------------

// TestWave70_GenerateEnvelope_WithLeadContext exercises the filesystem load path
// when lead-context.md, calibration.md, and a sidecar.json exist.
func TestWave70_GenerateEnvelope_WithLeadContext(t *testing.T) {
	db := wave70ContextDB(t)
	cm, contextDir := wave70CM(t, db)

	domain := "wave70-domain"
	project := "wave70-project"
	domainDir := filepath.Join(contextDir, domain)
	projectDir := filepath.Join(domainDir, project)
	os.MkdirAll(projectDir, 0755)

	// Write lead-context.md
	os.WriteFile(filepath.Join(domainDir, "lead-context.md"), []byte("# Lead\n\nsome context"), 0644)

	// Write calibration.md in project dir
	os.WriteFile(filepath.Join(projectDir, "calibration.md"), []byte("# Calibration\n"), 0644)

	// Write failures.json in domain dir
	failures := []Failure{{ID: "f70", Description: "test failure", Timestamp: time.Now()}}
	failuresJSON, _ := json.Marshal(failures)
	os.WriteFile(filepath.Join(domainDir, "failures.json"), failuresJSON, 0644)

	ctx := context.Background()
	env, err := cm.GenerateEnvelope(ctx, "agent-70", domain, project, "task-70", "wave70 reason")
	if err != nil {
		t.Fatalf("GenerateEnvelope: %v", err)
	}

	if env.Metadata["lead_context"] == nil {
		t.Error("expected lead_context in metadata")
	}
	if env.Calibration["main"] == "" {
		t.Error("expected calibration main to be populated")
	}
	if len(env.Failures) == 0 {
		t.Error("expected failures to be loaded from domain dir")
	}
}

// TestWave70_GenerateEnvelope_WithSidecar exercises the sidecar.json load path.
func TestWave70_GenerateEnvelope_WithSidecar(t *testing.T) {
	db := wave70ContextDB(t)

	tmpDir := t.TempDir()
	contextDir := filepath.Join(tmpDir, "context")
	os.MkdirAll(contextDir, 0755)

	// Create sidecar file relative to working dir.
	// loadFromFilesystem reads from ".forge/agents/<agentID>/sidecar.json"
	// relative to cwd — so we create it in the actual process cwd.
	cwd, _ := os.Getwd()
	agentID := "sidecar-agent-70"
	sidecarDir := filepath.Join(cwd, ".forge", "agents", agentID)
	os.MkdirAll(sidecarDir, 0755)
	defer os.RemoveAll(filepath.Join(cwd, ".forge", "agents", agentID))

	sidecar := map[string]interface{}{
		"agent_id":     agentID,
		"context_pct":  75.5,
		"timestamp":    time.Now().Unix(),
		"current_task": "task-sidecar",
		"status":       "working",
	}
	sidecarBytes, _ := json.Marshal(sidecar)
	os.WriteFile(filepath.Join(sidecarDir, "sidecar.json"), sidecarBytes, 0644)

	cm := &ContextManager{db: db, contextDir: contextDir}
	ctx := context.Background()
	env, err := cm.GenerateEnvelope(ctx, agentID, "sidecar-dom", "sidecar-proj", "sidecar-task", "sidecar test")
	if err != nil {
		t.Fatalf("GenerateEnvelope with sidecar: %v", err)
	}

	if env.Metadata["context_pct"] == nil {
		t.Error("expected context_pct from sidecar in metadata")
	}
	if env.Metadata["status"] != "working" {
		t.Errorf("expected status=working from sidecar, got %v", env.Metadata["status"])
	}
}

// TestWave70_GenerateEnvelope_ProjectDecisionsFallback tests when project decisions.json
// doesn't exist and it falls back to domain-level.
func TestWave70_GenerateEnvelope_ProjectDecisionsFallback(t *testing.T) {
	db := wave70ContextDB(t)
	cm, contextDir := wave70CM(t, db)

	domain := "fallback-domain"
	project := "fallback-project"
	domainDir := filepath.Join(contextDir, domain)
	projectDir := filepath.Join(domainDir, project)
	os.MkdirAll(projectDir, 0755)

	// Only write decisions in domain dir (not project dir)
	decisions := []Decision{{ID: "d-dom", Description: "domain decision", Timestamp: time.Now()}}
	decisionsJSON, _ := json.Marshal(decisions)
	os.WriteFile(filepath.Join(domainDir, "decisions.json"), decisionsJSON, 0644)

	ctx := context.Background()
	env, err := cm.GenerateEnvelope(ctx, "agent-fallback", domain, project, "task-fallback", "fallback test")
	if err != nil {
		t.Fatalf("GenerateEnvelope fallback: %v", err)
	}

	if len(env.Decisions) == 0 || env.Decisions[0].ID != "d-dom" {
		t.Errorf("expected domain-level decisions, got %+v", env.Decisions)
	}
}

// ---------------------------------------------------------------------------
// context.go: EnvelopesHandler — cover branches not yet exercised
// ---------------------------------------------------------------------------

// TestWave70_EnvelopesHandler_GET_Empty exercises the GET list path with empty DB.
func TestWave70_EnvelopesHandler_GET_Empty(t *testing.T) {
	db := wave70ContextDB(t)
	cm, _ := wave70CM(t, db)

	req, _ := http.NewRequest("GET", "/api/context/envelopes", nil)
	rr := httptest.NewRecorder()
	cm.EnvelopesHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
	// Should be valid JSON (null or [])
	var result interface{}
	if err := json.Unmarshal(rr.Body.Bytes(), &result); err != nil {
		t.Fatalf("response not valid JSON: %v — body: %s", err, rr.Body.String())
	}
}

// TestWave70_EnvelopesHandler_GET_WithData exercises GET with rows in DB.
func TestWave70_EnvelopesHandler_GET_WithData(t *testing.T) {
	db := wave70ContextDB(t)
	cm, _ := wave70CM(t, db)

	env := ContextEnvelope{
		ID: "env-get-70", AgentID: "ag", Domain: "dom", Project: "p",
		TaskID: "t", Summary: "s", Metadata: map[string]any{},
	}
	content, _ := json.Marshal(env)
	db.Exec(`INSERT INTO context_envelopes (id,agent_id,domain,project,task_id,summary,content,expires_at) VALUES (?,?,?,?,?,?,?,datetime('now','+1 day'))`,
		env.ID, env.AgentID, env.Domain, env.Project, env.TaskID, env.Summary, string(content))

	req, _ := http.NewRequest("GET", "/api/context/envelopes", nil)
	rr := httptest.NewRecorder()
	cm.EnvelopesHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
	var envs []ContextEnvelope
	json.NewDecoder(rr.Body).Decode(&envs)
	if len(envs) == 0 {
		t.Error("expected at least one envelope in response")
	}
}

// TestWave70_EnvelopesHandler_MethodNotAllowed covers the 405 branch.
func TestWave70_EnvelopesHandler_MethodNotAllowed(t *testing.T) {
	db := wave70ContextDB(t)
	cm, _ := wave70CM(t, db)

	req, _ := http.NewRequest("DELETE", "/api/context/envelopes", nil)
	rr := httptest.NewRecorder()
	cm.EnvelopesHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", rr.Code)
	}
}

// TestWave70_EnvelopesHandler_BadBody covers the 400 JSON decode error branch.
func TestWave70_EnvelopesHandler_BadBody(t *testing.T) {
	db := wave70ContextDB(t)
	cm, _ := wave70CM(t, db)

	req, _ := http.NewRequest("POST", "/api/context/envelopes", bytes.NewBufferString("not-json"))
	rr := httptest.NewRecorder()
	cm.EnvelopesHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rr.Code)
	}
}

// TestWave70_EnvelopesHandler_MissingTaskID covers the missing/invalid task_id validation.
func TestWave70_EnvelopesHandler_MissingTaskID(t *testing.T) {
	db := wave70ContextDB(t)
	cm, _ := wave70CM(t, db)

	body := []byte(`{"agent_id":"ag","domain":"dom","project":"proj","reason":"r"}`)
	req, _ := http.NewRequest("POST", "/api/context/envelopes", bytes.NewBuffer(body))
	rr := httptest.NewRecorder()
	cm.EnvelopesHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rr.Code)
	}
}

// TestWave70_EnvelopesHandler_InvalidAgentID covers the invalid agent_id path.
func TestWave70_EnvelopesHandler_InvalidAgentID(t *testing.T) {
	db := wave70ContextDB(t)
	cm, _ := wave70CM(t, db)

	body, _ := json.Marshal(CreateEnvelopeRequest{
		AgentID: "invalid agent id with spaces!",
		Domain:  "dom",
		Project: "proj",
		TaskID:  "task-70",
		Reason:  "test",
	})
	req, _ := http.NewRequest("POST", "/api/context/envelopes", bytes.NewBuffer(body))
	rr := httptest.NewRecorder()
	cm.EnvelopesHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid agent_id, got %d", rr.Code)
	}
}

// TestWave70_EnvelopesHandler_AgentIDFromHeader covers X-Agent-ID header fallback.
func TestWave70_EnvelopesHandler_AgentIDFromHeader(t *testing.T) {
	db := wave70ContextDB(t)
	cm, _ := wave70CM(t, db)

	body, _ := json.Marshal(CreateEnvelopeRequest{
		// AgentID intentionally empty — should come from header
		Domain:  "dom",
		Project: "proj",
		TaskID:  "task-header-70",
		Reason:  "header test",
	})
	req, _ := http.NewRequest("POST", "/api/context/envelopes", bytes.NewBuffer(body))
	req.Header.Set("X-Agent-ID", "header-agent")
	rr := httptest.NewRecorder()
	cm.EnvelopesHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200 with X-Agent-ID header, got %d: %s", rr.Code, rr.Body.String())
	}
}

// TestWave70_EnvelopesHandler_InvalidDomain covers invalid domain format.
func TestWave70_EnvelopesHandler_InvalidDomain(t *testing.T) {
	db := wave70ContextDB(t)
	cm, _ := wave70CM(t, db)

	body, _ := json.Marshal(CreateEnvelopeRequest{
		AgentID: "agent-70",
		Domain:  "invalid domain!!",
		Project: "proj",
		TaskID:  "task-70",
		Reason:  "test",
	})
	req, _ := http.NewRequest("POST", "/api/context/envelopes", bytes.NewBuffer(body))
	rr := httptest.NewRecorder()
	cm.EnvelopesHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid domain, got %d", rr.Code)
	}
}

// TestWave70_EnvelopesHandler_InvalidProject covers invalid project format.
func TestWave70_EnvelopesHandler_InvalidProject(t *testing.T) {
	db := wave70ContextDB(t)
	cm, _ := wave70CM(t, db)

	body, _ := json.Marshal(CreateEnvelopeRequest{
		AgentID: "agent-70",
		Domain:  "dom",
		Project: "invalid project!!",
		TaskID:  "task-70",
		Reason:  "test",
	})
	req, _ := http.NewRequest("POST", "/api/context/envelopes", bytes.NewBuffer(body))
	rr := httptest.NewRecorder()
	cm.EnvelopesHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid project, got %d", rr.Code)
	}
}
