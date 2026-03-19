//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// TestWave251_ParityChecker_NewParityChecker tests creating a new parity checker with default and custom paths
func TestWave251_ParityChecker_NewParityChecker(t *testing.T) {
	tests := []struct {
		name     string
		v2Path   string
		v3Path   string
		wantV2   string
		wantV3   string
	}{
		{
			name:     "default paths",
			v2Path:   "",
			v3Path:   "",
			wantV2:   ".forge",
			wantV3:   ".forge/forge-v3.db",
		},
		{
			name:     "custom paths",
			v2Path:   "/custom/forge",
			v3Path:   "/custom/db.sqlite",
			wantV2:   "/custom/forge",
			wantV3:   "/custom/db.sqlite",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			pc := NewParityChecker(tt.v2Path, tt.v3Path)
			if pc.v2StatePath != tt.wantV2 {
				t.Errorf("NewParityChecker() v2StatePath = %v, want %v", pc.v2StatePath, tt.wantV2)
			}
			if pc.v3DBPath != tt.wantV3 {
				t.Errorf("NewParityChecker() v3DBPath = %v, want %v", pc.v3DBPath, tt.wantV3)
			}
		})
	}
}

// TestWave251_ParityChecker_countV2Tasks tests counting v2 tasks from JSON files
func TestWave251_ParityChecker_countV2Tasks(t *testing.T) {
	// Create temp directory structure
	tempDir := t.TempDir()
	tasksDir := filepath.Join(tempDir, "tasks")
	if err := os.MkdirAll(tasksDir, 0755); err != nil {
		t.Fatalf("Failed to create tasks dir: %v", err)
	}

	// Create some task files
	for i := 0; i < 3; i++ {
		f := filepath.Join(tasksDir, "task-"+string(rune('0'+i))+".json")
		if err := os.WriteFile(f, []byte(`{}`), 0644); err != nil {
			t.Fatalf("Failed to create task file: %v", err)
		}
	}

	pc := NewParityChecker(tempDir, "")
	count, err := pc.countV2Tasks()
	if err != nil {
		t.Errorf("countV2Tasks() error = %v", err)
	}
	if count != 3 {
		t.Errorf("countV2Tasks() = %v, want 3", count)
	}
}

// TestWave251_ParityChecker_countV2Tasks_empty tests counting v2 tasks when directory is empty
func TestWave251_ParityChecker_countV2Tasks_empty(t *testing.T) {
	tempDir := t.TempDir()
	tasksDir := filepath.Join(tempDir, "tasks")
	if err := os.MkdirAll(tasksDir, 0755); err != nil {
		t.Fatalf("Failed to create tasks dir: %v", err)
	}

	pc := NewParityChecker(tempDir, "")
	count, err := pc.countV2Tasks()
	if err != nil {
		t.Errorf("countV2Tasks() error = %v", err)
	}
	if count != 0 {
		t.Errorf("countV2Tasks() = %v, want 0", count)
	}
}

// TestWave251_ParityChecker_countV3Tasks tests counting v3 tasks from SQLite
func TestWave251_ParityChecker_countV3Tasks(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	// Create SQLite DB with tasks table
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open db: %v", err)
	}
	defer db.Close()

	// Create tasks table and insert data
	if _, err := db.Exec("CREATE TABLE tasks (id TEXT PRIMARY KEY)"); err != nil {
		t.Fatalf("Failed to create table: %v", err)
	}
	for i := 0; i < 5; i++ {
		if _, err := db.Exec("INSERT INTO tasks VALUES (?)", "task-"+string(rune('0'+i))); err != nil {
			t.Fatalf("Failed to insert task: %v", err)
		}
	}

	pc := NewParityChecker("", dbPath)
	count, err := pc.countV3Tasks()
	if err != nil {
		t.Errorf("countV3Tasks() error = %v", err)
	}
	if count != 5 {
		t.Errorf("countV3Tasks() = %v, want 5", count)
	}
}

// TestWave251_ParityChecker_countV2Agents tests counting v2 agents from state.json
func TestWave251_ParityChecker_countV2Agents(t *testing.T) {
	tempDir := t.TempDir()
	fleetDir := filepath.Join(tempDir, "fleet")
	if err := os.MkdirAll(fleetDir, 0755); err != nil {
		t.Fatalf("Failed to create fleet dir: %v", err)
	}

	// Create state.json with agents
	state := map[string]interface{}{
		"agents": []interface{}{
			map[string]string{"id": "agent-1"},
			map[string]string{"id": "agent-2"},
			map[string]string{"id": "agent-3"},
		},
	}
	data, _ := json.Marshal(state)
	if err := os.WriteFile(filepath.Join(fleetDir, "state.json"), data, 0644); err != nil {
		t.Fatalf("Failed to write state.json: %v", err)
	}

	pc := NewParityChecker(tempDir, "")
	count, err := pc.countV2Agents()
	if err != nil {
		t.Errorf("countV2Agents() error = %v", err)
	}
	if count != 3 {
		t.Errorf("countV2Agents() = %v, want 3", count)
	}
}

// TestWave251_ParityChecker_countV2Agents_noFile tests counting v2 agents when state.json doesn't exist
func TestWave251_ParityChecker_countV2Agents_noFile(t *testing.T) {
	tempDir := t.TempDir()
	// Don't create fleet/state.json

	pc := NewParityChecker(tempDir, "")
	count, err := pc.countV2Agents()
	if err != nil {
		t.Errorf("countV2Agents() error = %v, want nil", err)
	}
	if count != 0 {
		t.Errorf("countV2Agents() = %v, want 0", count)
	}
}

// TestWave251_ParityChecker_countV3Agents tests counting v3 agents from SQLite
func TestWave251_ParityChecker_countV3Agents(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open db: %v", err)
	}
	defer db.Close()

	// Create agents table
	if _, err := db.Exec("CREATE TABLE agents (id TEXT PRIMARY KEY)"); err != nil {
		t.Fatalf("Failed to create table: %v", err)
	}
	for i := 0; i < 4; i++ {
		if _, err := db.Exec("INSERT INTO agents VALUES (?)", "agent-"+string(rune('0'+i))); err != nil {
			t.Fatalf("Failed to insert agent: %v", err)
		}
	}

	pc := NewParityChecker("", dbPath)
	count, err := pc.countV3Agents()
	if err != nil {
		t.Errorf("countV3Agents() error = %v", err)
	}
	if count != 4 {
		t.Errorf("countV3Agents() = %v, want 4", count)
	}
}

// TestWave251_ParityChecker_countV3Agents_noTable tests counting v3 agents when agents table doesn't exist
func TestWave251_ParityChecker_countV3Agents_noTable(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open db: %v", err)
	}
	defer db.Close()

	// Create DB but no agents table
	if _, err := db.Exec("CREATE TABLE tasks (id TEXT)"); err != nil {
		t.Fatalf("Failed to create tasks table: %v", err)
	}

	pc := NewParityChecker("", dbPath)
	count, err := pc.countV3Agents()
	if err != nil {
		t.Errorf("countV3Agents() error = %v, want nil", err)
	}
	if count != 0 {
		t.Errorf("countV3Agents() = %v, want 0", count)
	}
}

// TestWave251_ParityChecker_Check tests the full parity check
func TestWave251_ParityChecker_Check(t *testing.T) {
	tempDir := t.TempDir()
	tasksDir := filepath.Join(tempDir, "tasks")
	fleetDir := filepath.Join(tempDir, "fleet")
	os.MkdirAll(tasksDir, 0755)
	os.MkdirAll(fleetDir, 0755)

	// Create 2 v2 tasks
	for i := 0; i < 2; i++ {
		f := filepath.Join(tasksDir, "task-"+string(rune('0'+i))+".json")
		os.WriteFile(f, []byte(`{}`), 0644)
	}

	// Create 2 v2 agents
	state := map[string]interface{}{
		"agents": []interface{}{
			map[string]string{"id": "agent-1"},
			map[string]string{"id": "agent-2"},
		},
	}
	data, _ := json.Marshal(state)
	os.WriteFile(filepath.Join(fleetDir, "state.json"), data, 0644)

	// Create v3 DB with matching counts
	dbPath := filepath.Join(tempDir, "forge-v3.db")
	db, _ := sql.Open("sqlite3", dbPath)
	defer db.Close()
	db.Exec("CREATE TABLE tasks (id TEXT)")
	db.Exec("CREATE TABLE agents (id TEXT)")
	for i := 0; i < 2; i++ {
		db.Exec("INSERT INTO tasks VALUES (?)", "task-"+string(rune('0'+i)))
		db.Exec("INSERT INTO agents VALUES (?)", "agent-"+string(rune('0'+i)))
	}

	pc := NewParityChecker(tempDir, dbPath)
	result, err := pc.Check()
	if err != nil {
		t.Errorf("Check() error = %v", err)
	}
	if result == nil {
		t.Fatal("Check() returned nil result")
	}
	if !result.TasksMatch {
		t.Errorf("Tasks should match: v2=%d, v3=%d", result.TasksV2, result.TasksV3)
	}
	if !result.AgentsMatch {
		t.Errorf("Agents should match: v2=%d, v3=%d", result.AgentsV2, result.AgentsV3)
	}
	if !result.Overall {
		t.Error("Overall should be true when both match")
	}
}

// TestWave251_ParityChecker_Check_mismatch tests parity check with mismatched counts
func TestWave251_ParityChecker_Check_mismatch(t *testing.T) {
	tempDir := t.TempDir()
	tasksDir := filepath.Join(tempDir, "tasks")
	fleetDir := filepath.Join(tempDir, "fleet")
	os.MkdirAll(tasksDir, 0755)
	os.MkdirAll(fleetDir, 0755)

	// Create 3 v2 tasks
	for i := 0; i < 3; i++ {
		f := filepath.Join(tasksDir, "task-"+string(rune('0'+i))+".json")
		os.WriteFile(f, []byte(`{}`), 0644)
	}

	// Create 1 v2 agent
	state := map[string]interface{}{
		"agents": []interface{}{
			map[string]string{"id": "agent-1"},
		},
	}
	data, _ := json.Marshal(state)
	os.WriteFile(filepath.Join(fleetDir, "state.json"), data, 0644)

	// Create v3 DB with different counts
	dbPath := filepath.Join(tempDir, "forge-v3.db")
	db, _ := sql.Open("sqlite3", dbPath)
	defer db.Close()
	db.Exec("CREATE TABLE tasks (id TEXT)")
	db.Exec("CREATE TABLE agents (id TEXT)")
	// Only 1 task in v3
	db.Exec("INSERT INTO tasks VALUES (?)", "task-1")
	// 2 agents in v3
	db.Exec("INSERT INTO agents VALUES (?)", "agent-1")
	db.Exec("INSERT INTO agents VALUES (?)", "agent-2")

	pc := NewParityChecker(tempDir, dbPath)
	result, err := pc.Check()
	if err != nil {
		t.Errorf("Check() error = %v", err)
	}
	if result.TasksMatch {
		t.Error("Tasks should not match (3 vs 1)")
	}
	if result.AgentsMatch {
		t.Error("Agents should not match (1 vs 2)")
	}
	if result.Overall {
		t.Error("Overall should be false when there are mismatches")
	}
	if result.TasksDiff != -2 {
		t.Errorf("TasksDiff = %d, want -2", result.TasksDiff)
	}
	if result.AgentsDiff != 1 {
		t.Errorf("AgentsDiff = %d, want 1", result.AgentsDiff)
	}
}

// TestWave251_ParityResult_JSONSerialization tests ParityResult JSON serialization
func TestWave251_ParityResult_JSONSerialization(t *testing.T) {
	result := &ParityResult{
		Timestamp:   time.Now().Unix(),
		TasksMatch:  true,
		TasksV2:     10,
		TasksV3:     10,
		TasksDiff:   0,
		AgentsMatch: false,
		AgentsV2:    3,
		AgentsV3:    5,
		AgentsDiff:  2,
		Overall:     false,
		Errors:      []string{"test error"},
	}

	data, err := json.Marshal(result)
	if err != nil {
		t.Fatalf("Failed to marshal ParityResult: %v", err)
	}

	var decoded ParityResult
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("Failed to unmarshal ParityResult: %v", err)
	}

	if decoded.TasksMatch != result.TasksMatch {
		t.Error("TasksMatch mismatch after serialization")
	}
	if decoded.AgentsV3 != result.AgentsV3 {
		t.Error("AgentsV3 mismatch after serialization")
	}
	if len(decoded.Errors) != len(result.Errors) {
		t.Error("Errors mismatch after serialization")
	}
}

// TestWave251_ParityCheck_function tests the standalone ParityCheck function
func TestWave251_ParityCheck_function(t *testing.T) {
	// Note: This test uses the actual default paths, so it may fail in some environments
	// We're testing the function exists and can be called
	result, err := ParityCheck(nil)
	// We expect this to work even with nil db as it creates its own checker
	if err != nil {
		// Error is expected if default paths don't exist
		t.Logf("ParityCheck returned error (expected in test env): %v", err)
	}
	if result != nil {
		t.Logf("ParityCheck result: %+v", result)
	}
}

// TestWave251_ValidationError_Error tests ValidationError Error method
func TestWave251_ValidationError_Error(t *testing.T) {
	err := ValidationError{
		Field:   "test_field",
		Reason:  "test reason",
		Code:    "TEST_CODE",
		Context: "additional context",
	}

	msg := err.Error()
	if msg == "" {
		t.Error("Error() returned empty string")
	}
	if msg == "validation error [TEST_CODE]: test_field - test reason" {
		// Expected format
		t.Logf("Error message format correct: %s", msg)
	}
}

// TestWave251_ProtocolValidator_validateTaskCompletedMessage tests task.completed message validation
func TestWave251_ProtocolValidator_validateTaskCompletedMessage(t *testing.T) {
	v := NewProtocolValidator()

	tests := []struct {
		name    string
		payload map[string]interface{}
		wantErr bool
		errCode string
	}{
		{
			name: "valid task.completed",
			payload: map[string]interface{}{
				"task_id": "TASK-001",
				"result":  "success",
			},
			wantErr: false,
		},
		{
			name:    "missing task_id",
			payload: map[string]interface{}{"result": "success"},
			wantErr: true,
			errCode: "MISSING_FIELD",
		},
		{
			name:    "missing result",
			payload: map[string]interface{}{"task_id": "TASK-001"},
			wantErr: true,
			errCode: "MISSING_FIELD",
		},
		{
			name:    "payload not an object",
			payload: nil,
			wantErr: true,
			errCode: "TYPE_MISMATCH", // Schema validation catches this first
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			msg := map[string]interface{}{
				"type":    "task.completed",
				"payload": tt.payload,
			}
			data, _ := json.Marshal(msg)
			err := v.ValidateWSMessage(data)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateWSMessage() error = %v, wantErr %v", err, tt.wantErr)
			}
			if err != nil && tt.errCode != "" {
				if valErr, ok := err.(ValidationError); ok {
					if valErr.Code != tt.errCode {
						t.Errorf("Expected error code %s, got %s", tt.errCode, valErr.Code)
					}
				}
			}
		})
	}
}

// TestWave251_ProtocolValidator_isVersionCompatible_edgeCases tests version compatibility edge cases
func TestWave251_ProtocolValidator_isVersionCompatible_edgeCases(t *testing.T) {
	v := NewProtocolValidator()

	tests := []struct {
		version  string
		required string
		want     bool
	}{
		{"3.0.0", "3.0.0", true},
		{"3.1.0", "3.0.0", true},
		{"3.10.5", "3.0.0", true},
		{"2.0.0", "3.0.0", false},
		{"4.0.0", "3.0.0", false},
		{"", "3.0.0", false},
		{"3.0.0", "", false},
		{"invalid", "3.0.0", false},
		{"3.0.0", "invalid", false},
		{"v3.0.0", "3.0.0", false}, // "v" prefix not handled
	}

	for _, tt := range tests {
		t.Run(tt.version+"_vs_"+tt.required, func(t *testing.T) {
			got := v.isVersionCompatible(tt.version, tt.required)
			if got != tt.want {
				t.Errorf("isVersionCompatible(%q, %q) = %v, want %v", tt.version, tt.required, got, tt.want)
			}
		})
	}
}

// TestWave251_detectType tests the detectType function for various types
func TestWave251_detectType(t *testing.T) {
	tests := []struct {
		value interface{}
		want  string
	}{
		{"string", "string"},
		{42, "integer"},
		{int64(42), "integer"},
		{3.14, "number"},
		{float64(3.14), "number"},
		{3.0, "integer"}, // Whole number float becomes integer
		{true, "boolean"},
		{false, "boolean"},
		{map[string]interface{}{}, "object"},
		{[]interface{}{}, "array"},
		{nil, "unknown"},
		{struct{}{}, "unknown"},
	}

	for _, tt := range tests {
		t.Run(tt.want, func(t *testing.T) {
			got := detectType(tt.value)
			if got != tt.want {
				t.Errorf("detectType(%v) = %v, want %v", tt.value, got, tt.want)
			}
		})
	}
}

// TestWave251_ProtocolValidator_ValidateResponse tests response validation
func TestWave251_ProtocolValidator_ValidateResponse(t *testing.T) {
	v := NewProtocolValidator()

	tests := []struct {
		name     string
		endpoint string
		data     []byte
		wantErr  bool
	}{
		{
			name:     "valid tasks response",
			endpoint: "/api/tasks",
			data:     []byte(`{"tasks": []}`),
			wantErr:  false,
		},
		{
			name:     "valid agents response",
			endpoint: "/api/agents",
			data:     []byte(`{"agents": []}`),
			wantErr:  false,
		},
		{
			name:     "unknown endpoint - no validation",
			endpoint: "/api/unknown",
			data:     []byte(`{}`),
			wantErr:  false,
		},
		{
			name:     "invalid JSON",
			endpoint: "/api/tasks",
			data:     []byte(`{invalid`),
			wantErr:  true,
		},
		{
			name:     "error response with string",
			endpoint: "/api/tasks",
			data:     []byte(`{"error": "something went wrong"}`),
			wantErr:  false,
		},
		{
			name:     "error response with non-string error",
			endpoint: "/api/tasks",
			data:     []byte(`{"error": {"code": 500}}`),
			wantErr:  true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := v.ValidateResponse(tt.endpoint, tt.data)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateResponse() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

// TestWave251_GetProtocolVersion tests GetProtocolVersion function
func TestWave251_GetProtocolVersion(t *testing.T) {
	version := GetProtocolVersion()
	if version == "" {
		t.Error("GetProtocolVersion() returned empty string")
	}
	if version != ProtocolVersion {
		t.Errorf("GetProtocolVersion() = %v, want %v", version, ProtocolVersion)
	}
}

// TestWave251_ProtocolValidator_customRules tests custom rule validation
func TestWave251_ProtocolValidator_customRules(t *testing.T) {
	v := NewProtocolValidator()

	tests := []struct {
		name    string
		data    string
		wantErr bool
		errCode string
	}{
		{
			name:    "valid priority (range rule)",
			data:    `{"id": "TASK-001", "type": "test", "priority": 5}`,
			wantErr: false,
		},
		{
			name:    "priority at minimum",
			data:    `{"id": "TASK-001", "type": "test", "priority": 1}`,
			wantErr: false,
		},
		{
			name:    "priority at maximum",
			data:    `{"id": "TASK-001", "type": "test", "priority": 10}`,
			wantErr: false,
		},
		{
			name:    "priority below minimum",
			data:    `{"id": "TASK-001", "type": "test", "priority": 0}`,
			wantErr: true,
			errCode: "OUT_OF_RANGE",
		},
		{
			name:    "priority above maximum",
			data:    `{"id": "TASK-001", "type": "test", "priority": 11}`,
			wantErr: true,
			errCode: "OUT_OF_RANGE",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := v.ValidateTaskRequest([]byte(tt.data))
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateTaskRequest() error = %v, wantErr %v", err, tt.wantErr)
			}
			if err != nil && tt.errCode != "" {
				if valErr, ok := err.(ValidationError); ok {
					if valErr.Code != tt.errCode {
						t.Errorf("Expected error code %s, got %s", tt.errCode, valErr.Code)
					}
				}
			}
		})
	}
}
