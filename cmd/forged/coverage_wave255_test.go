//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// TestWave255_LoadLaneConfig_Success tests loading a valid lane config file
func TestWave255_LoadLaneConfig_Success(t *testing.T) {
	// Create a temporary config file
	tmpDir := t.TempDir()
	configPath := filepath.Join(tmpDir, "lane_config.yaml")

	configContent := `
lanes:
  dev:
    gates:
      - lint
      - test
    thresholds:
      coverage: 80.0
      max_lint_issues: 10
    auto_progress: true
    requires_approval: false
    timeout: 30m
  prod:
    gates:
      - lint
      - test
      - security
    thresholds:
      coverage: 90.0
      max_lint_issues: 0
    auto_progress: false
    requires_approval: true
    timeout: 1h
`
	if err := os.WriteFile(configPath, []byte(configContent), 0644); err != nil {
		t.Fatalf("failed to write config file: %v", err)
	}

	// Load the config
	configs, err := LoadLaneConfig(configPath)
	if err != nil {
		t.Errorf("LoadLaneConfig error: %v", err)
	}

	// Verify configs loaded
	if len(configs) != 2 {
		t.Errorf("expected 2 lane configs, got %d", len(configs))
	}

	// Verify dev lane
	devConfig, ok := configs["dev"]
	if !ok {
		t.Error("dev lane not found in config")
	}
	if len(devConfig.Gates) != 2 {
		t.Errorf("expected 2 gates for dev, got %d", len(devConfig.Gates))
	}
	if devConfig.Thresholds.Coverage != 80.0 {
		t.Errorf("expected coverage 80.0, got %f", devConfig.Thresholds.Coverage)
	}
	if devConfig.AutoProgress != true {
		t.Error("expected auto_progress true for dev")
	}
	if devConfig.RequiresApproval != false {
		t.Error("expected requires_approval false for dev")
	}

	// Verify prod lane
	prodConfig, ok := configs["prod"]
	if !ok {
		t.Error("prod lane not found in config")
	}
	if len(prodConfig.Gates) != 3 {
		t.Errorf("expected 3 gates for prod, got %d", len(prodConfig.Gates))
	}
	if prodConfig.Thresholds.Coverage != 90.0 {
		t.Errorf("expected coverage 90.0, got %f", prodConfig.Thresholds.Coverage)
	}
	if prodConfig.RequiresApproval != true {
		t.Error("expected requires_approval true for prod")
	}
}

// TestWave255_LoadLaneConfig_FileNotFound tests error handling for missing file
func TestWave255_LoadLaneConfig_FileNotFound(t *testing.T) {
	_, err := LoadLaneConfig("/nonexistent/path/config.yaml")
	if err == nil {
		t.Error("expected error for non-existent file")
	}
}

// TestWave255_LoadLaneConfig_InvalidYAML tests error handling for invalid YAML
func TestWave255_LoadLaneConfig_InvalidYAML(t *testing.T) {
	tmpDir := t.TempDir()
	configPath := filepath.Join(tmpDir, "invalid.yaml")

	invalidContent := `
lanes:
  - this is not valid
    structure: [
      unclosed bracket
`
	if err := os.WriteFile(configPath, []byte(invalidContent), 0644); err != nil {
		t.Fatalf("failed to write config file: %v", err)
	}

	_, err := LoadLaneConfig(configPath)
	if err == nil {
		t.Error("expected error for invalid YAML")
	}
}

// TestWave255_LoadLaneConfig_EmptyConfig tests loading an empty config
func TestWave255_LoadLaneConfig_EmptyConfig(t *testing.T) {
	tmpDir := t.TempDir()
	configPath := filepath.Join(tmpDir, "empty.yaml")

	emptyContent := `lanes: {}`
	if err := os.WriteFile(configPath, []byte(emptyContent), 0644); err != nil {
		t.Fatalf("failed to write config file: %v", err)
	}

	configs, err := LoadLaneConfig(configPath)
	if err != nil {
		t.Errorf("LoadLaneConfig error: %v", err)
	}
	if len(configs) != 0 {
		t.Errorf("expected 0 lane configs, got %d", len(configs))
	}
}

// TestWave255_LoadLaneConfig_DurationParsing tests duration parsing in config
func TestWave255_LoadLaneConfig_DurationParsing(t *testing.T) {
	tmpDir := t.TempDir()
	configPath := filepath.Join(tmpDir, "duration.yaml")

	configContent := `
lanes:
  test:
    gates: []
    thresholds:
      coverage: 75.0
    timeout: 45m
`
	if err := os.WriteFile(configPath, []byte(configContent), 0644); err != nil {
		t.Fatalf("failed to write config file: %v", err)
	}

	configs, err := LoadLaneConfig(configPath)
	if err != nil {
		t.Errorf("LoadLaneConfig error: %v", err)
	}

	config, ok := configs["test"]
	if !ok {
		t.Fatal("test lane not found")
	}

	expectedDuration := 45 * time.Minute
	if config.Timeout != expectedDuration {
		t.Errorf("expected timeout %v, got %v", expectedDuration, config.Timeout)
	}
}

// TestWave255_MinInt tests the minInt function
func TestWave255_MinInt(t *testing.T) {
	tests := []struct {
		a, b, expected int
	}{
		{1, 2, 1},
		{2, 1, 1},
		{5, 5, 5},
		{-1, 1, -1},
		{-5, -10, -10},
		{0, 100, 0},
		{100, 0, 0},
	}

	for _, tt := range tests {
		result := minInt(tt.a, tt.b)
		if result != tt.expected {
			t.Errorf("minInt(%d, %d) = %d, expected %d", tt.a, tt.b, result, tt.expected)
		}
	}
}

// TestWave255_PriorityToInt tests the priorityToInt function
func TestWave255_PriorityToInt(t *testing.T) {
	tests := []struct {
		priority string
		expected int
	}{
		{"critical", 1},
		{"high", 2},
		{"medium", 3},
		{"low", 4},
		{"unknown", 5},
		{"", 5},
		{"URGENT", 5},
		{"normal", 5},
	}

	for _, tt := range tests {
		result := priorityToInt(tt.priority)
		if result != tt.expected {
			t.Errorf("priorityToInt(%q) = %d, expected %d", tt.priority, result, tt.expected)
		}
	}
}

// TestWave255_IsValidID tests the isValidID function
func TestWave255_IsValidID(t *testing.T) {
	tests := []struct {
		id       string
		expected bool
	}{
		{"valid-id", true},
		{"valid_id", true},
		{"valid:id", true},
		{"ValidID123", true},
		{"test-agent-001", true},
		{"", false},
		{"has spaces", false},
		{"has@symbol", false},
		{"has#hash", false},
		{"has!exclamation", false},
	}

	for _, tt := range tests {
		result := isValidID(tt.id)
		if result != tt.expected {
			t.Errorf("isValidID(%q) = %v, expected %v", tt.id, result, tt.expected)
		}
	}
}

// TestWave255_IsValidID_Length tests ID length validation
func TestWave255_IsValidID_Length(t *testing.T) {
	// Test empty string
	if isValidID("") {
		t.Error("empty string should not be valid ID")
	}

	// Test 128 character string (should be valid if alphanumeric)
	valid128 := "a"
	for i := 0; i < 127; i++ {
		valid128 += "b"
	}
	if len(valid128) != 128 {
		t.Fatalf("test setup error: expected 128 chars, got %d", len(valid128))
	}
	if !isValidID(valid128) {
		t.Error("128 character alphanumeric ID should be valid")
	}

	// Test 129 character string (should be invalid)
	invalid129 := valid128 + "c"
	if isValidID(invalid129) {
		t.Error("129 character ID should be invalid")
	}
}

// TestWave255_SanitizeID tests the sanitizeID function
func TestWave255_SanitizeID(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{"valid-id", "valid-id"},
		{"  trim-spaces  ", "trim-spaces"},
		{"\ttrim-tabs\n", "trim-tabs"},
		{"no-extra-spaces", "no-extra-spaces"},
	}

	for _, tt := range tests {
		result := sanitizeID(tt.input)
		if result != tt.expected {
			t.Errorf("sanitizeID(%q) = %q, expected %q", tt.input, result, tt.expected)
		}
	}
}

// TestWave255_IsValidText tests the isValidText function
func TestWave255_IsValidText(t *testing.T) {
	tests := []struct {
		text     string
		expected bool
	}{
		{"valid text", true},
		{"Valid_Text-123", true},
		{"with.dots,and:colons", true},
		{"with(parens)[brackets]", true},
		{"with!exclamation", true},
		{"", true}, // Empty is allowed
		{"normal sentence with spaces", true},
		{"has@symbol", false},
		{"has#hash", false},
		{"has$dollar", false},
		{"has%percent", false},
		{"has^caret", false},
		{"has&ampersand", false},
		{"has*star", false},
		{"has+plus", false},
		{"has=equals", false},
		{"has{brace}", false},
		{"has|pipe", false},
		{"has<less>", false},
		{"has>greater", false},
		{"has?question", false},
		{"has'singlequote", false},
		{"has\"doublequote\"", false},
		{"has`backtick", false},
		{"has~tilde", false},
	}

	for _, tt := range tests {
		result := isValidText(tt.text)
		if result != tt.expected {
			t.Errorf("isValidText(%q) = %v, expected %v", tt.text, result, tt.expected)
		}
	}
}

// TestWave255_IsValidText_Length tests text length validation
func TestWave255_IsValidText_Length(t *testing.T) {
	// Test empty string (allowed)
	if !isValidText("") {
		t.Error("empty string should be valid text")
	}

	// Test 2048 character string (should be valid)
	valid2048 := ""
	for i := 0; i < 2048; i++ {
		valid2048 += "a"
	}
	if len(valid2048) != 2048 {
		t.Fatalf("test setup error: expected 2048 chars, got %d", len(valid2048))
	}
	if !isValidText(valid2048) {
		t.Error("2048 character text should be valid")
	}

	// Test 2049 character string (should be invalid)
	invalid2049 := valid2048 + "b"
	if isValidText(invalid2049) {
		t.Error("2049 character text should be invalid")
	}
}

// TestWave255_SanitizeText tests the sanitizeText function
func TestWave255_SanitizeText(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{"valid text", "valid text"},
		{"  trim spaces  ", "trim spaces"},
		{"\ttrim tabs\n", "trim tabs"},
		{"no change needed", "no change needed"},
	}

	for _, tt := range tests {
		result := sanitizeText(tt.input)
		if result != tt.expected {
			t.Errorf("sanitizeText(%q) = %q, expected %q", tt.input, result, tt.expected)
		}
	}
}

// TestWave255_IsTitleJunk tests the isTitleJunk function
func TestWave255_IsTitleJunk(t *testing.T) {
	tests := []struct {
		title    string
		expected bool
	}{
		// Short titles (< 10 chars) are junk
		{"short", true},
		{"tiny", true},
		{"fix", true},
		{"", true},
		{"123456789", true}, // 9 chars, still junk

		// Known junk patterns
		{"forge/dispatch", true},
		{"openclaw/bridge", true},
		{"fix bug", true},
		{"update code", true},
		{"add feature", true},
		{"some feature (feature)", true},
		{"some bug (bug)", true},

		// Good titles (not junk)
		{"Implement user authentication system", false},
		{"Fix critical security vulnerability in API", false},
		{"Update documentation for new features", false},
		{"Add comprehensive test coverage", false},
		{"This is a very long title that describes the work in detail and should not be considered junk", false},
		{"FORGE-123: Add new endpoint for task management", false},
	}

	for _, tt := range tests {
		result := isTitleJunk(tt.title)
		if result != tt.expected {
			t.Errorf("isTitleJunk(%q) = %v, expected %v", tt.title, result, tt.expected)
		}
	}
}

// TestWave255_IsTitleJunk_CaseInsensitive tests case insensitivity
func TestWave255_IsTitleJunk_CaseInsensitive(t *testing.T) {
	tests := []struct {
		title    string
		expected bool
	}{
		{"FORGE/DISPATCH", true},
		{"Forge/Dispatch", true},
		{"OPENCLAW/BRIDGE", true},
		{"Fix Bug", true},
		{"FIX BUG", true},
		{"UPDATE CODE", true},
		{"ADD FEATURE", true},
	}

	for _, tt := range tests {
		result := isTitleJunk(tt.title)
		if result != tt.expected {
			t.Errorf("isTitleJunk(%q) = %v, expected %v", tt.title, result, tt.expected)
		}
	}
}

// TestWave255_DBConn tests getDBConn and setDBConn functions
func TestWave255_DBConn(t *testing.T) {
	// Save original connection
	originalConn := getDBConn()
	defer setDBConn(originalConn) // Restore after test

	// Test with nil
	setDBConn(nil)
	if getDBConn() != nil {
		t.Error("expected nil connection after setDBConn(nil)")
	}

	// Create a test database connection
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test.db")
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("failed to open test db: %v", err)
	}
	defer db.Close()

	// Set the connection
	setDBConn(db)

	// Verify we get the same connection back
	retrievedConn := getDBConn()
	if retrievedConn != db {
		t.Error("retrieved connection is not the same as set connection")
	}

	// Verify connection is working
	if err := retrievedConn.Ping(); err != nil {
		t.Errorf("retrieved connection ping failed: %v", err)
	}
}

// TestWave255_DBConn_ConcurrentAccess tests concurrent access to dbConn
func TestWave255_DBConn_ConcurrentAccess(t *testing.T) {
	// Save original
	originalConn := getDBConn()
	defer setDBConn(originalConn)

	// Create test connections
	tmpDir := t.TempDir()
	db1, _ := sql.Open("sqlite3", filepath.Join(tmpDir, "test1.db"))
	db2, _ := sql.Open("sqlite3", filepath.Join(tmpDir, "test2.db"))
	defer db1.Close()
	defer db2.Close()

	// Set initial
	setDBConn(db1)

	// Concurrent reads and writes
	done := make(chan bool, 10)

	// Writers
	for i := 0; i < 5; i++ {
		go func() {
			setDBConn(db1)
			done <- true
		}()
		go func() {
			setDBConn(db2)
			done <- true
		}()
	}

	// Readers
	for i := 0; i < 5; i++ {
		go func() {
			_ = getDBConn()
			done <- true
		}()
	}

	// Wait for all goroutines
	for i := 0; i < 10; i++ {
		<-done
	}

	// Connection should be set to one of them
	conn := getDBConn()
	if conn != db1 && conn != db2 {
		t.Error("connection should be either db1 or db2")
	}
}

// TestWave255_PopulateTasksFromFeatures tests the stub function
func TestWave255_PopulateTasksFromFeatures(t *testing.T) {
	err := PopulateTasksFromFeatures("/tmp/db", "/tmp/features.json")
	if err == nil {
		t.Error("expected error from stub function")
	}
	if err.Error() != "populate-tasks command has been removed (dead code cleanup); use forge task create instead" {
		t.Errorf("unexpected error message: %v", err)
	}
}

// TestWave255_PopulateTasksFromDispatches tests the stub function
func TestWave255_PopulateTasksFromDispatches(t *testing.T) {
	err := PopulateTasksFromDispatches("/tmp/db", "/tmp/dispatches")
	if err == nil {
		t.Error("expected error from stub function")
	}
	if err.Error() != "import-dispatches command has been removed (dead code cleanup); use forge task create instead" {
		t.Errorf("unexpected error message: %v", err)
	}
}
