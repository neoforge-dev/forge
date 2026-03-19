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

func setupContextTestDB(t *testing.T) *sql.DB {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("failed to open in-memory db: %v", err)
	}

	_, err = db.Exec(`
		CREATE TABLE context_envelopes (
			id TEXT PRIMARY KEY,
			agent_id TEXT NOT NULL,
			domain TEXT NOT NULL,
			project TEXT NOT NULL,
			task_id TEXT NOT NULL,
			summary TEXT,
			content TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			expires_at TIMESTAMP NOT NULL
		);
	`)
	if err != nil {
		t.Fatalf("failed to create table: %v", err)
	}

	return db
}

func TestContextManager(t *testing.T) {
	db := setupContextTestDB(t)
	defer db.Close()

	tmpDir, err := os.MkdirTemp("", "forge-context-test")
	if err != nil {
		t.Fatalf("failed to create tmp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	cm := testContextManager(t, db, tmpDir)
	defer cm.Stop()

	// Prepare some filesystem data
	domain := "test-domain"
	domainDir := filepath.Join(tmpDir, domain)
	if err := os.MkdirAll(domainDir, 0755); err != nil {
		t.Fatalf("failed to create domain dir: %v", err)
	}

	decisions := []Decision{
		{ID: "d1", Description: "dec1", Reasoning: "res1", Timestamp: time.Now()},
	}
	decisionsData, _ := json.Marshal(decisions)
	os.WriteFile(filepath.Join(domainDir, "decisions.json"), decisionsData, 0644)

	failures := []Failure{
		{ID: "f1", Description: "fail1", Lesson: "les1", Timestamp: time.Now()},
	}
	failuresData, _ := json.Marshal(failures)
	os.WriteFile(filepath.Join(domainDir, "failures.json"), failuresData, 0644)

	calibration := "calibration data"
	os.WriteFile(filepath.Join(domainDir, "calibration.md"), []byte(calibration), 0644)

	ctx := context.Background()
	agentID := "test-agent"
	project := "test-project"
	taskID := "test-task"
	reason := "testing context"

	// Test Generate Envelope with project-specific data
	projectDir := filepath.Join(domainDir, project)
	if err := os.MkdirAll(projectDir, 0755); err != nil {
		t.Fatalf("failed to create project dir: %v", err)
	}

	projectDecisions := []Decision{
		{ID: "pd1", Description: "project dec1", Reasoning: "pres1", Timestamp: time.Now()},
	}
	projectDecisionsData, _ := json.Marshal(projectDecisions)
	os.WriteFile(filepath.Join(projectDir, "decisions.json"), projectDecisionsData, 0644)

	envelope, err := cm.GenerateEnvelope(ctx, agentID, domain, project, taskID, reason)
	if err != nil {
		t.Fatalf("GenerateEnvelope failed: %v", err)
	}

	if len(envelope.Decisions) != 1 || envelope.Decisions[0].ID != "pd1" {
		t.Errorf("project-specific decisions not loaded: %+v", envelope.Decisions)
	}

	if envelope.AgentID != agentID || envelope.Domain != domain || envelope.Summary != reason {
		t.Errorf("envelope field mismatch: %+v", envelope)
	}

	// Load failures from domain dir (since they are not in project dir)
	if len(envelope.Failures) != 1 || envelope.Failures[0].ID != "f1" {
		t.Errorf("failures not loaded correctly: %+v", envelope.Failures)
	}

	if envelope.Calibration["main"] != calibration {
		t.Errorf("calibration not loaded correctly: %s", envelope.Calibration["main"])
	}

	// Verify it's in DB
	var dbContent string
	err = db.QueryRow("SELECT content FROM context_envelopes WHERE id = ?", envelope.ID).Scan(&dbContent)
	if err != nil {
		t.Fatalf("failed to query envelope from db: %v", err)
	}

	var dbEnvelope ContextEnvelope
	json.Unmarshal([]byte(dbContent), &dbEnvelope)
	if dbEnvelope.ID != envelope.ID {
		t.Errorf("db envelope ID mismatch: %s != %s", dbEnvelope.ID, envelope.ID)
	}

	// Verify it's on filesystem
	envelopeFilePath := filepath.Join(tmpDir, "envelopes", envelope.ID+".json")
	if _, err := os.Stat(envelopeFilePath); os.IsNotExist(err) {
		t.Errorf("envelope file not found: %s", envelopeFilePath)
	}

	// Test BootstrapAgent
	bootstrapEnvelope, err := cm.BootstrapAgent(ctx, agentID, taskID)
	if err != nil {
		t.Fatalf("BootstrapAgent failed: %v", err)
	}

	if bootstrapEnvelope.ID != envelope.ID {
		t.Errorf("bootstrap envelope ID mismatch: %s != %s", bootstrapEnvelope.ID, envelope.ID)
	}

	// Test HTTP Handlers

	// 1. EnvelopesHandler
	reqBody, _ := json.Marshal(CreateEnvelopeRequest{
		AgentID: agentID,
		Domain:  domain,
		Project: project,
		TaskID:  taskID,
		Reason:  "http request test",
	})
	req, _ := http.NewRequest("POST", "/api/context/envelopes", bytes.NewBuffer(reqBody))
	rr := httptest.NewRecorder()
	cm.EnvelopesHandler(rr, req)

	if status := rr.Code; status != http.StatusOK {
		t.Errorf("handler returned wrong status code: got %v want %v", status, http.StatusOK)
	}

	var httpEnvelope ContextEnvelope
	json.NewDecoder(rr.Body).Decode(&httpEnvelope)
	if httpEnvelope.Summary != "http request test" {
		t.Errorf("http envelope summary mismatch: %s", httpEnvelope.Summary)
	}

	// 2. EnvelopeByIDHandler
	req, _ = http.NewRequest("GET", "/api/context/envelopes/"+httpEnvelope.ID, nil)
	rr = httptest.NewRecorder()
	// Need to simulate mux routing or manually trim prefix
	cm.EnvelopeByIDHandler(rr, req)

	if status := rr.Code; status != http.StatusOK {
		t.Errorf("handler returned wrong status code: got %v want %v", status, http.StatusOK)
	}

	// 3. BootstrapHandler
	bootstrapReqBody, _ := json.Marshal(BootstrapRequest{
		AgentID: agentID,
		TaskID:  taskID,
	})
	req, _ = http.NewRequest("POST", "/api/context/bootstrap", bytes.NewBuffer(bootstrapReqBody))
	rr = httptest.NewRecorder()
	cm.BootstrapHandler(rr, req)

	if status := rr.Code; status != http.StatusOK {
		t.Errorf("handler returned wrong status code: got %v want %v", status, http.StatusOK)
	}

	var finalEnvelope ContextEnvelope
	json.NewDecoder(rr.Body).Decode(&finalEnvelope)
	if finalEnvelope.AgentID != agentID {
		t.Errorf("bootstrap envelope agent_id mismatch: %s", finalEnvelope.AgentID)
	}

	// 4. BootstrapHandler via GET with query parameters (for curl convenience)
	req, _ = http.NewRequest("GET", "/api/context/bootstrap?agent_id="+agentID+"&task_id="+taskID, nil)
	rr = httptest.NewRecorder()
	cm.BootstrapHandler(rr, req)

	if status := rr.Code; status != http.StatusOK {
		t.Errorf("GET bootstrap handler returned wrong status code: got %v want %v", status, http.StatusOK)
	}

	var getEnvelope ContextEnvelope
	json.NewDecoder(rr.Body).Decode(&getEnvelope)
	if getEnvelope.AgentID != agentID {
		t.Errorf("GET bootstrap envelope agent_id mismatch: %s", getEnvelope.AgentID)
	}
}

// TestEnvelopesHandler_MinimalPayload verifies that the envelope generation
// endpoint accepts a minimal POST body with only task_id and fills defaults.
func TestEnvelopesHandler_MinimalPayload(t *testing.T) {
	db := setupContextTestDB(t)
	defer db.Close()

	tmpDir, err := os.MkdirTemp("", "forge-context-test-minimal")
	if err != nil {
		t.Fatalf("failed to create tmp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	cm := testContextManager(t, db, tmpDir)
	defer cm.Stop()

	body := []byte(`{"task_id":"minimal-task"}`)
	req, _ := http.NewRequest("POST", "/api/context/envelope", bytes.NewBuffer(body))
	rr := httptest.NewRecorder()

	cm.EnvelopesHandler(rr, req)

	if status := rr.Code; status != http.StatusOK {
		t.Fatalf("expected 200 OK, got %d", status)
	}

	var env ContextEnvelope
	if err := json.NewDecoder(rr.Body).Decode(&env); err != nil {
		t.Fatalf("failed to decode envelope: %v", err)
	}

	if env.TaskID != "minimal-task" {
		t.Fatalf("expected TaskID minimal-task, got %s", env.TaskID)
	}
	if env.Domain == "" || env.Project == "" {
		t.Fatalf("expected default domain/project to be set, got %q/%q", env.Domain, env.Project)
	}
}

func TestContextManager_SyncEnvelopesToFilesystem_NilSync(t *testing.T) {
	db := setupContextTestDB(t)
	defer db.Close()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()
	cm.sync = nil
	err := cm.SyncEnvelopesToFilesystem()
	if err == nil {
		t.Error("expected error when sync is nil")
	}
}

func TestContextManager_SyncDomainToFilesystem_NilSync(t *testing.T) {
	db := setupContextTestDB(t)
	defer db.Close()
	cm := testContextManager(t, db, t.TempDir())
	defer cm.Stop()
	cm.sync = nil
	err := cm.SyncDomainToFilesystem("forge")
	if err == nil {
		t.Error("expected error when sync is nil")
	}
}
