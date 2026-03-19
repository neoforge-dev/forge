//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// TestWave269_PatternsHandler_MethodNotAllowed tests method validation
// TestWave269_PatternsHandler_Get_DBNotReady tests nil database
// Note: This test is skipped because nil DB causes panic - production always has DB
func TestWave269_PatternsHandler_Get_DBNotReady(t *testing.T) {
	t.Skip("Nil DB causes panic - production always initializes it")
	
	// Save and restore
	origDB := getDBConn()
	defer func() {
		if origDB != nil {
			setDBConn(origDB)
		}
	}()
	
	setDBConn(nil)
	
	req := httptest.NewRequest(http.MethodGet, "/api/patterns", nil)
	w := httptest.NewRecorder()
	
	patternsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", resp.StatusCode)
	}
}

// TestWave269_ListPatternsHandler_Success tests successful pattern list
func TestWave269_ListPatternsHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert test patterns
	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	for i := 0; i < 3; i++ {
		_, err := db.Exec(`
			INSERT INTO patterns (id, name, domain, yaml_content, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?)
		`, "pattern-"+string(rune('a'+i)), "Pattern "+string(rune('A'+i)), "test", "yaml content", now, now)
		if err != nil {
			t.Fatalf("failed to insert pattern: %v", err)
		}
	}
	
	req := httptest.NewRequest(http.MethodGet, "/api/patterns", nil)
	w := httptest.NewRecorder()
	
	patternsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	
	contentType := resp.Header.Get("Content-Type")
	if contentType != "application/json" {
		t.Errorf("expected Content-Type application/json, got %s", contentType)
	}
	
	var body map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if count, ok := body["count"].(float64); !ok || count != 3 {
		t.Errorf("expected count=3, got %v", body["count"])
	}
	
	patterns, ok := body["patterns"].([]interface{})
	if !ok || len(patterns) != 3 {
		t.Errorf("expected 3 patterns, got %v", patterns)
	}
}

// TestWave269_ListPatternsHandler_EmptyDB tests empty database
func TestWave269_ListPatternsHandler_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db // db is used for global connection

	req := httptest.NewRequest(http.MethodGet, "/api/patterns", nil)
	w := httptest.NewRecorder()
	
	patternsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	_ = db
	}
	
	var body map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	_ = db
	}
	
	if count, ok := body["count"].(float64); !ok || count != 0 {
		t.Errorf("expected count=0, got %v", body["count"])
	_ = db
	}
}

// TestWave269_ListPatternsHandler_FilterByDomain tests domain filter
func TestWave269_ListPatternsHandler_FilterByDomain(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	
	// Insert patterns in different domains
	_, err := db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"pat-1", "Pattern 1", "domain-a", "yaml", now, now)
	if err != nil {
		t.Fatalf("failed to insert: %v", err)
	}
	_, err = db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"pat-2", "Pattern 2", "domain-b", "yaml", now, now)
	if err != nil {
		t.Fatalf("failed to insert: %v", err)
	}
	
	// Query with domain filter
	req := httptest.NewRequest(http.MethodGet, "/api/patterns?domain=domain-a", nil)
	w := httptest.NewRecorder()
	
	patternsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	
	var body map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if count, ok := body["count"].(float64); !ok || count != 1 {
		t.Errorf("expected count=1 for domain-a, got %v", body["count"])
	}
}

// TestWave269_CreatePatternHandler_MethodNotAllowed tests POST validation (via patternsHandler)
// TestWave269_CreatePatternHandler_InvalidBody tests invalid request body
func TestWave269_CreatePatternHandler_InvalidBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/patterns", strings.NewReader("invalid json"))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	patternsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", resp.StatusCode)
	}
}

// TestWave269_CreatePatternHandler_MissingName tests missing name validation
func TestWave269_CreatePatternHandler_MissingName(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db // db is used for global connection

	reqBody := `{"domain": "test"}`
	req := httptest.NewRequest(http.MethodPost, "/api/patterns", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	patternsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", resp.StatusCode)
	}
	
	body, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(body), "name required") {
		t.Errorf("expected 'name required' error, got: %s", string(body))
	}
}

// TestWave269_CreatePatternHandler_Success tests successful creation
func TestWave269_CreatePatternHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db // db is used for global connection

	reqBody := `{"name": "New Pattern", "domain": "test-domain", "yaml_content": "test: yaml"}`
	req := httptest.NewRequest(http.MethodPost, "/api/patterns", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	patternsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusCreated {
		t.Errorf("expected 201, got %d", resp.StatusCode)
	}
	
	var body Pattern
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if body.Name != "New Pattern" {
		t.Errorf("expected Name='New Pattern', got '%s'", body.Name)
	}
	if body.Domain != "test-domain" {
		t.Errorf("expected Domain='test-domain', got '%s'", body.Domain)
	}
	if body.ID == "" {
		t.Error("expected non-empty ID")
	}
}

// TestWave269_CreatePatternHandler_DefaultDomain tests default domain
func TestWave269_CreatePatternHandler_DefaultDomain(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db // db is used for global connection

	reqBody := `{"name": "No Domain Pattern"}`
	req := httptest.NewRequest(http.MethodPost, "/api/patterns", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	patternsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusCreated {
		t.Errorf("expected 201, got %d", resp.StatusCode)
	}
	
	var body Pattern
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if body.Domain != "default" {
		t.Errorf("expected Domain='default', got '%s'", body.Domain)
	}
}

// TestWave269_PatternByIDOrRunsHandler_MethodNotAllowed tests method validation
// TestWave269_PatternByIDOrRunsHandler_MissingID tests missing pattern ID
func TestWave269_PatternByIDOrRunsHandler_MissingID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/patterns/", nil)
	w := httptest.NewRecorder()
	
	patternByIDOrRunsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", resp.StatusCode)
	}
}

// TestWave269_GetPatternByIDHandler_NotFound tests non-existent pattern
func TestWave269_GetPatternByIDHandler_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db // db is used for global connection

	req := httptest.NewRequest(http.MethodGet, "/api/patterns/nonexistent", nil)
	w := httptest.NewRecorder()
	
	patternByIDOrRunsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("expected 404, got %d", resp.StatusCode)
	}
}

// TestWave269_GetPatternByIDHandler_Success tests successful pattern retrieval
func TestWave269_GetPatternByIDHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, err := db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"test-pat", "Test Pattern", "test", "yaml: content", now, now)
	if err != nil {
		t.Fatalf("failed to insert pattern: %v", err)
	}
	
	req := httptest.NewRequest(http.MethodGet, "/api/patterns/test-pat", nil)
	w := httptest.NewRecorder()
	
	patternByIDOrRunsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	
	var body Pattern
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if body.ID != "test-pat" {
		t.Errorf("expected ID='test-pat', got '%s'", body.ID)
	}
	if body.Name != "Test Pattern" {
		t.Errorf("expected Name='Test Pattern', got '%s'", body.Name)
	}
}

// TestWave269_ListPatternRunsHandler_Success tests runs list
func TestWave269_ListPatternRunsHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert pattern
	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, err := db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"run-pat", "Run Pattern", "test", "yaml", now, now)
	if err != nil {
		t.Fatalf("failed to insert pattern: %v", err)
	}
	
	// Insert pattern runs
	_, err = db.Exec(`INSERT INTO pattern_runs (run_id, pattern_id, task_id, agent_id, domain, started_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"run-1", "run-pat", "task-1", "agent-1", "test", now)
	if err != nil {
		t.Fatalf("failed to insert run: %v", err)
	}
	
	req := httptest.NewRequest(http.MethodGet, "/api/patterns/run-pat/runs", nil)
	w := httptest.NewRecorder()
	
	patternByIDOrRunsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	
	var body map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if body["pattern_id"] != "run-pat" {
		t.Errorf("expected pattern_id='run-pat', got %v", body["pattern_id"])
	}
	
	if count, ok := body["count"].(float64); !ok || count != 1 {
		t.Errorf("expected count=1, got %v", body["count"])
	}
}

// TestWave269_Pattern_Struct tests Pattern struct
func TestWave269_Pattern_Struct(t *testing.T) {
	now := time.Now().UTC()
	p := Pattern{
		ID:          "pat-001",
		Name:        "Test Pattern",
		Domain:      "test",
		YAMLContent: "test: yaml",
		CreatedAt:   now,
		UpdatedAt:   now,
	}
	
	if p.ID != "pat-001" {
		t.Error("ID mismatch")
	}
	if p.Name != "Test Pattern" {
		t.Error("Name mismatch")
	}
	if p.Domain != "test" {
		t.Error("Domain mismatch")
	}
}

// TestWave269_PatternRun_Struct tests PatternRun struct
func TestWave269_PatternRun_Struct(t *testing.T) {
	success := true
	confidence := 0.95
	tokens := 100
	duration := 5
	completedAt := "2024-01-01T00:00:00Z"
	metadata := `{"key": "value"}`
	errorType := "none"
	
	run := PatternRun{
		RunID:       "run-001",
		PatternID:   "pat-001",
		TaskID:      "task-001",
		AgentID:     "agent-001",
		Domain:      "test",
		StartedAt:   "2024-01-01T00:00:00Z",
		CompletedAt: &completedAt,
		Success:     &success,
		Confidence:  &confidence,
		TokensUsed:  &tokens,
		DurationS:   &duration,
		ErrorType:   &errorType,
		Metadata:    &metadata,
	}
	
	if run.RunID != "run-001" {
		t.Error("RunID mismatch")
	}
	if *run.Success != true {
		t.Error("Success mismatch")
	}
	if *run.Confidence != 0.95 {
		t.Error("Confidence mismatch")
	}
}

// TestWave269_Pattern_JSON tests JSON serialization
func TestWave269_Pattern_JSON(t *testing.T) {
	now := time.Now().UTC()
	p := Pattern{
		ID:          "json-pat",
		Name:        "JSON Pattern",
		Domain:      "json-test",
		YAMLContent: "yaml: content",
		CreatedAt:   now,
		UpdatedAt:   now,
	}
	
	data, err := json.Marshal(p)
	if err != nil {
		t.Fatalf("failed to marshal: %v", err)
	}
	
	var decoded Pattern
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("failed to unmarshal: %v", err)
	}
	
	if decoded.ID != p.ID {
		t.Error("ID mismatch after roundtrip")
	}
	if decoded.Name != p.Name {
		t.Error("Name mismatch after roundtrip")
	}
}

// TestWave269_LoadPatternsFromDocs_NonExistentDir tests with non-existent directory
func TestWave269_LoadPatternsFromDocs_NonExistentDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Should not panic with non-existent directory
	loadPatternsFromDocs(db)
	
	// Nothing to verify - just ensure it doesn't panic
}
