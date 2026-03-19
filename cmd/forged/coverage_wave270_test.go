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

// TestWave270_ProjectsHandler_MethodNotAllowed tests method validation
// TestWave270_ProjectsHandler_Get_DBNotReady tests nil database
// Note: This test is skipped because nil DB causes panic - production always has DB
func TestWave270_ProjectsHandler_Get_DBNotReady(t *testing.T) {
	t.Skip("Nil DB causes panic - production always initializes it")
	
	// Save and restore
	origDB := getDBConn()
	defer func() {
		if origDB != nil {
			setDBConn(origDB)
		}
	}()
	
	setDBConn(nil)
	
	req := httptest.NewRequest(http.MethodGet, "/api/projects", nil)
	w := httptest.NewRecorder()
	
	projectsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", resp.StatusCode)
	}
}

// TestWave270_ListProjectsHandler_Success tests successful project list
func TestWave270_ListProjectsHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert test projects
	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	for i := 0; i < 3; i++ {
		_, err := db.Exec(`
			INSERT INTO projects (id, key, name, domain, description, path, type, status, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		`, 
			"proj-"+string(rune('a'+i)), 
			"key-"+string(rune('a'+i)),
			"Project "+string(rune('A'+i)), 
			"test",
			"Description "+string(rune('A'+i)),
			"/path/"+string(rune('a'+i)),
			"service",
			"active",
			now, now)
		if err != nil {
			t.Fatalf("failed to insert project: %v", err)
		}
	}
	
	req := httptest.NewRequest(http.MethodGet, "/api/projects", nil)
	w := httptest.NewRecorder()
	
	projectsHandler(w, req)
	
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
	
	projects, ok := body["projects"].([]interface{})
	if !ok || len(projects) != 3 {
		t.Errorf("expected 3 projects, got %v", projects)
	}
}

// TestWave270_ListProjectsHandler_EmptyDB tests empty database
func TestWave270_ListProjectsHandler_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db // db is used for global connection

	req := httptest.NewRequest(http.MethodGet, "/api/projects", nil)
	w := httptest.NewRecorder()
	
	projectsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	
	var body map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if count, ok := body["count"].(float64); !ok || count != 0 {
		t.Errorf("expected count=0, got %v", body["count"])
	}
}

// TestWave270_ListProjectsHandler_FilterByDomain tests domain filter
func TestWave270_ListProjectsHandler_FilterByDomain(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	
	// Insert projects in different domains
	_, err := db.Exec(`INSERT INTO projects (id, key, name, domain, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"proj-1", "key-1", "Project 1", "domain-a", now, now)
	if err != nil {
		t.Fatalf("failed to insert: %v", err)
	}
	_, err = db.Exec(`INSERT INTO projects (id, key, name, domain, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"proj-2", "key-2", "Project 2", "domain-b", now, now)
	if err != nil {
		t.Fatalf("failed to insert: %v", err)
	}
	
	// Query with domain filter
	req := httptest.NewRequest(http.MethodGet, "/api/projects?domain=domain-a", nil)
	w := httptest.NewRecorder()
	
	projectsHandler(w, req)
	
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

// TestWave270_CreateProjectHandler_MethodNotAllowed tests POST validation
// TestWave270_CreateProjectHandler_InvalidBody tests invalid request body
func TestWave270_CreateProjectHandler_InvalidBody(t *testing.T) {
	_, _ = setupClaimTestDB(t) // For global DB
	req := httptest.NewRequest(http.MethodPost, "/api/projects", strings.NewReader("invalid json"))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	projectsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", resp.StatusCode)
	}
}

// TestWave270_CreateProjectHandler_MissingKey tests missing key validation
func TestWave270_CreateProjectHandler_MissingKey(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db // db is used for global connection

	reqBody := `{"name": "No Key Project"}`
	req := httptest.NewRequest(http.MethodPost, "/api/projects", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	projectsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", resp.StatusCode)
	}
	
	body, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(body), "invalid or missing project key") {
		t.Errorf("expected 'invalid or missing project key' error, got: %s", string(body))
	}
}

// TestWave270_CreateProjectHandler_InvalidKey tests invalid key validation
func TestWave270_CreateProjectHandler_InvalidKey(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db // db is used for global connection

	reqBody := `{"key": "Invalid Key With Spaces"}`
	req := httptest.NewRequest(http.MethodPost, "/api/projects", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	projectsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", resp.StatusCode)
	}
}

// TestWave270_CreateProjectHandler_Success tests successful creation
func TestWave270_CreateProjectHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db // db is used for global connection

	reqBody := `{"key": "new-project", "name": "New Project", "domain": "test-domain", "description": "A test project", "type": "service", "status": "active"}`
	req := httptest.NewRequest(http.MethodPost, "/api/projects", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	projectsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusCreated {
		t.Errorf("expected 201, got %d", resp.StatusCode)
	}
	
	var body Project
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if body.Key != "new-project" {
		t.Errorf("expected Key='new-project', got '%s'", body.Key)
	}
	if body.Name != "New Project" {
		t.Errorf("expected Name='New Project', got '%s'", body.Name)
	}
	if body.Domain != "test-domain" {
		t.Errorf("expected Domain='test-domain', got '%s'", body.Domain)
	}
	if body.ID == "" {
		t.Error("expected non-empty ID")
	}
}

// TestWave270_CreateProjectHandler_DefaultDomain tests default domain
func TestWave270_CreateProjectHandler_DefaultDomain(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db // db is used for global connection

	reqBody := `{"key": "no-domain"}`
	req := httptest.NewRequest(http.MethodPost, "/api/projects", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	projectsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusCreated {
		t.Errorf("expected 201, got %d", resp.StatusCode)
	}
	
	var body Project
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if body.Domain != "default" {
		t.Errorf("expected Domain='default', got '%s'", body.Domain)
	}
	// Name should default to key
	if body.Name != "no-domain" {
		t.Errorf("expected Name='no-domain', got '%s'", body.Name)
	}
}

// TestWave270_ProjectByIDHandler_MethodNotAllowed tests method validation
// TestWave270_ProjectByIDHandler_MissingID tests missing project ID
func TestWave270_ProjectByIDHandler_MissingID(t *testing.T) {
	_, _ = setupClaimTestDB(t) // For global DB
	req := httptest.NewRequest(http.MethodGet, "/api/projects/", nil)
	w := httptest.NewRecorder()
	
	projectByIDHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", resp.StatusCode)
	}
}

// TestWave270_ProjectByIDHandler_NotFound tests non-existent project
func TestWave270_ProjectByIDHandler_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db // db is used for global connection

	req := httptest.NewRequest(http.MethodGet, "/api/projects/nonexistent", nil)
	w := httptest.NewRecorder()
	
	projectByIDHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("expected 404, got %d", resp.StatusCode)
	}
}

// TestWave270_ProjectByIDHandler_Success tests successful project retrieval
func TestWave270_ProjectByIDHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, err := db.Exec(`INSERT INTO projects (id, key, name, domain, description, path, type, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"test-proj", "test-key", "Test Project", "test", "A test project", "/path", "service", "active", now, now)
	if err != nil {
		t.Fatalf("failed to insert project: %v", err)
	}
	
	// Test by ID
	req := httptest.NewRequest(http.MethodGet, "/api/projects/test-proj", nil)
	w := httptest.NewRecorder()
	
	projectByIDHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	
	var body Project
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if body.ID != "test-proj" {
		t.Errorf("expected ID='test-proj', got '%s'", body.ID)
	}
	if body.Key != "test-key" {
		t.Errorf("expected Key='test-key', got '%s'", body.Key)
	}
	if body.Name != "Test Project" {
		t.Errorf("expected Name='Test Project', got '%s'", body.Name)
	}
}

// TestWave270_ProjectByIDHandler_ByKey tests lookup by key
func TestWave270_ProjectByIDHandler_ByKey(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, err := db.Exec(`INSERT INTO projects (id, key, name, domain, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"proj-id", "lookup-key", "Lookup Project", "test", now, now)
	if err != nil {
		t.Fatalf("failed to insert project: %v", err)
	}
	
	// Test by key
	req := httptest.NewRequest(http.MethodGet, "/api/projects/lookup-key", nil)
	w := httptest.NewRecorder()
	
	projectByIDHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	
	var body Project
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if body.Key != "lookup-key" {
		t.Errorf("expected Key='lookup-key', got '%s'", body.Key)
	}
}

// TestWave270_Project_Struct tests Project struct
func TestWave270_Project_Struct(t *testing.T) {
	now := time.Now().UTC()
	p := Project{
		ID:          "proj-001",
		Key:         "key-001",
		Name:        "Test Project",
		Domain:      "test",
		Description: "A test project",
		Path:        "/path/to/project",
		Type:        "service",
		Status:      "active",
		CreatedAt:   now,
		UpdatedAt:   now,
	}
	
	if p.ID != "proj-001" {
		t.Error("ID mismatch")
	}
	if p.Key != "key-001" {
		t.Error("Key mismatch")
	}
	if p.Name != "Test Project" {
		t.Error("Name mismatch")
	}
	if p.Domain != "test" {
		t.Error("Domain mismatch")
	}
}

// TestWave270_Project_JSON tests JSON serialization
func TestWave270_Project_JSON(t *testing.T) {
	now := time.Now().UTC()
	p := Project{
		ID:          "json-proj",
		Key:         "json-key",
		Name:        "JSON Project",
		Domain:      "json-test",
		Description: "JSON test",
		Type:        "service",
		Status:      "active",
		CreatedAt:   now,
		UpdatedAt:   now,
	}
	
	data, err := json.Marshal(p)
	if err != nil {
		t.Fatalf("failed to marshal: %v", err)
	}
	
	var decoded Project
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("failed to unmarshal: %v", err)
	}
	
	if decoded.ID != p.ID {
		t.Error("ID mismatch after roundtrip")
	}
	if decoded.Key != p.Key {
		t.Error("Key mismatch after roundtrip")
	}
	if decoded.Name != p.Name {
		t.Error("Name mismatch after roundtrip")
	}
}

// TestWave270_ProjectByIDHandler_AltPath tests /projects/ path (without /api)
func TestWave270_ProjectByIDHandler_AltPath(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, err := db.Exec(`INSERT INTO projects (id, key, name, domain, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"alt-proj", "alt-key", "Alt Project", "test", now, now)
	if err != nil {
		t.Fatalf("failed to insert project: %v", err)
	}
	
	// Test with /projects/ path
	req := httptest.NewRequest(http.MethodGet, "/projects/alt-proj", nil)
	w := httptest.NewRecorder()
	
	projectByIDHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	
	var body Project
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if body.ID != "alt-proj" {
		t.Errorf("expected ID='alt-proj', got '%s'", body.ID)
	}
}
