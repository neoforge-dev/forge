//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
)

// ============================================================
// coverage_wave228_test.go — ForgeError types + config parsing
// Target: errors.go (all constructors + ExitCode), config.go (parseForgeTOML, configPaths)
// ============================================================

// --- ForgeError.Error() ---

func TestWave228_ForgeError_ErrorWithSuggestion(t *testing.T) {
	fe := &ForgeError{
		Code:       ErrCodeNotFound,
		Message:    "task not found",
		Suggestion: "check the ID",
	}
	got := fe.Error()
	if !strings.Contains(got, "NOT_FOUND") {
		t.Errorf("expected NOT_FOUND in error string, got: %s", got)
	}
	if !strings.Contains(got, "Suggestion") {
		t.Errorf("expected Suggestion in error string, got: %s", got)
	}
}

func TestWave228_ForgeError_ErrorNoSuggestion(t *testing.T) {
	fe := &ForgeError{
		Code:    ErrCodeInternal,
		Message: "internal failure",
	}
	got := fe.Error()
	if !strings.Contains(got, "INTERNAL_ERROR") {
		t.Errorf("expected INTERNAL_ERROR, got: %s", got)
	}
	if strings.Contains(got, "Suggestion") {
		t.Errorf("unexpected Suggestion in error without suggestion: %s", got)
	}
}

// --- ForgeError.JSON() ---

func TestWave228_ForgeError_JSON(t *testing.T) {
	fe := &ForgeError{
		Code:    ErrCodeTimeout,
		Message: "operation timed out",
		Details: "after 30s",
	}
	j := fe.JSON()
	if !strings.Contains(j, "TIMEOUT") {
		t.Errorf("expected TIMEOUT in JSON: %s", j)
	}
	if !strings.Contains(j, "after 30s") {
		t.Errorf("expected details in JSON: %s", j)
	}
}

// --- ForgeError.ExitCode() ---

func TestWave228_ExitCode_AllCodes(t *testing.T) {
	cases := []struct {
		code     ErrorCode
		expected int
	}{
		{ErrCodeNotFound, ExitNotFound},
		{ErrCodeInvalidState, ExitInvalidState},
		{ErrCodeUnauthorized, ExitUnauthorized},
		{ErrCodeLeaseExpired, ExitLeaseExpired},
		{ErrCodeDuplicate, ExitDuplicate},
		{ErrCodeTimeout, ExitTimeout},
		{ErrCodeValidation, ExitValidation},
		{ErrCodeInternal, ExitInternal},
		{ErrCodeUnavailable, ExitUnavailable},
		{"UNKNOWN_CODE", ExitGeneralError},
	}
	for _, tc := range cases {
		fe := &ForgeError{Code: tc.code}
		if got := fe.ExitCode(); got != tc.expected {
			t.Errorf("ExitCode(%s): expected %d, got %d", tc.code, tc.expected, got)
		}
	}
}

// --- IsForgeError ---

func TestWave228_IsForgeError_True(t *testing.T) {
	err := NewNotFoundError("task", "task-123")
	fe, ok := IsForgeError(err)
	if !ok {
		t.Fatal("expected IsForgeError=true")
	}
	if fe.Code != ErrCodeNotFound {
		t.Errorf("expected NOT_FOUND, got %s", fe.Code)
	}
}

func TestWave228_IsForgeError_False(t *testing.T) {
	err := errors.New("plain error")
	_, ok := IsForgeError(err)
	if ok {
		t.Error("expected IsForgeError=false for plain error")
	}
}

// --- Error constructors ---

func TestWave228_NewNotFoundError(t *testing.T) {
	err := NewNotFoundError("task", "T-001")
	if err == nil {
		t.Fatal("expected non-nil error")
	}
	if !strings.Contains(err.Error(), "NOT_FOUND") {
		t.Errorf("expected NOT_FOUND code: %s", err.Error())
	}
	if !strings.Contains(err.Error(), "T-001") {
		t.Errorf("expected ID in error: %s", err.Error())
	}
}

func TestWave228_NewInvalidStateError(t *testing.T) {
	err := NewInvalidStateError("T-002", "queued", "running")
	if !strings.Contains(err.Error(), "INVALID_STATE") {
		t.Errorf("expected INVALID_STATE: %s", err.Error())
	}
	fe, _ := IsForgeError(err)
	if fe.TaskID != "T-002" {
		t.Errorf("expected TaskID=T-002, got %s", fe.TaskID)
	}
}

func TestWave228_NewUnauthorizedError(t *testing.T) {
	err := NewUnauthorizedError("delete task")
	if !strings.Contains(err.Error(), "UNAUTHORIZED") {
		t.Errorf("expected UNAUTHORIZED: %s", err.Error())
	}
}

func TestWave228_NewLeaseExpiredError(t *testing.T) {
	err := NewLeaseExpiredError("T-003")
	if !strings.Contains(err.Error(), "LEASE_EXPIRED") {
		t.Errorf("expected LEASE_EXPIRED: %s", err.Error())
	}
	fe, _ := IsForgeError(err)
	if fe.TaskID != "T-003" {
		t.Errorf("expected TaskID=T-003, got %s", fe.TaskID)
	}
}

func TestWave228_NewDuplicateError(t *testing.T) {
	err := NewDuplicateError("agent", "forge:kimi")
	if !strings.Contains(err.Error(), "DUPLICATE") {
		t.Errorf("expected DUPLICATE: %s", err.Error())
	}
}

func TestWave228_NewTimeoutError(t *testing.T) {
	err := NewTimeoutError("task claim", "30s")
	if !strings.Contains(err.Error(), "TIMEOUT") {
		t.Errorf("expected TIMEOUT: %s", err.Error())
	}
}

func TestWave228_NewValidationError(t *testing.T) {
	err := NewValidationError("priority", "must be 1-10")
	if !strings.Contains(err.Error(), "VALIDATION_ERROR") {
		t.Errorf("expected VALIDATION_ERROR: %s", err.Error())
	}
}

func TestWave228_NewInternalError(t *testing.T) {
	err := NewInternalError("db connection failed")
	if !strings.Contains(err.Error(), "INTERNAL_ERROR") {
		t.Errorf("expected INTERNAL_ERROR: %s", err.Error())
	}
	fe, _ := IsForgeError(err)
	if fe.Details != "db connection failed" {
		t.Errorf("expected details, got: %s", fe.Details)
	}
}

func TestWave228_NewUnavailableError(t *testing.T) {
	err := NewUnavailableError("forged")
	if !strings.Contains(err.Error(), "SERVICE_UNAVAILABLE") {
		t.Errorf("expected SERVICE_UNAVAILABLE: %s", err.Error())
	}
}

// --- configPaths ---

func TestWave228_ConfigPaths_DefaultPaths(t *testing.T) {
	os.Unsetenv("FORGE_CONFIG")
	paths := configPaths()
	if len(paths) == 0 {
		t.Error("expected at least one config path")
	}
	// Should include .forge/forge.toml somewhere
	found := false
	for _, p := range paths {
		if strings.Contains(p, "forge.toml") {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("expected forge.toml in paths: %v", paths)
	}
}

func TestWave228_ConfigPaths_WithEnvOverride(t *testing.T) {
	os.Setenv("FORGE_CONFIG", "/custom/path/forge.toml")
	defer os.Unsetenv("FORGE_CONFIG")
	paths := configPaths()
	if paths[0] != "/custom/path/forge.toml" {
		t.Errorf("expected custom path first, got: %v", paths)
	}
}

// --- parseForgeTOML ---

func TestWave228_ParseForgeTOML_ValidConfig(t *testing.T) {
	content := `[daemon]
port = 9090
ws_port = 9091
db_path = "/tmp/test.db"
node_id = "prya"
forge_root = "/tmp/forge"

[auth]
mode = "api"
api_token = "tok-test"
`
	f, err := os.CreateTemp("", "forge-*.toml")
	if err != nil {
		t.Fatalf("create temp: %v", err)
	}
	defer os.Remove(f.Name())
	fmt.Fprint(f, content)
	f.Close()

	cfg, err := parseForgeTOML(f.Name())
	if err != nil {
		t.Fatalf("parseForgeTOML: %v", err)
	}
	if cfg.Daemon.Port != 9090 {
		t.Errorf("expected port=9090, got %d", cfg.Daemon.Port)
	}
	if cfg.Daemon.WSPort != 9091 {
		t.Errorf("expected ws_port=9091, got %d", cfg.Daemon.WSPort)
	}
	if cfg.Daemon.DBPath != "/tmp/test.db" {
		t.Errorf("expected db_path, got %s", cfg.Daemon.DBPath)
	}
	if cfg.Daemon.NodeID != "prya" {
		t.Errorf("expected node_id=prya, got %s", cfg.Daemon.NodeID)
	}
	if cfg.Auth.Mode != "api" {
		t.Errorf("expected mode=api, got %s", cfg.Auth.Mode)
	}
	if cfg.Auth.APIToken != "tok-test" {
		t.Errorf("expected api_token=tok-test, got %s", cfg.Auth.APIToken)
	}
}

func TestWave228_ParseForgeTOML_CommentsAndBlanks(t *testing.T) {
	content := `# This is a comment

[daemon]
# port comment
port = 8081
`
	f, err := os.CreateTemp("", "forge-*.toml")
	if err != nil {
		t.Fatalf("create temp: %v", err)
	}
	defer os.Remove(f.Name())
	fmt.Fprint(f, content)
	f.Close()

	cfg, err := parseForgeTOML(f.Name())
	if err != nil {
		t.Fatalf("parseForgeTOML: %v", err)
	}
	if cfg.Daemon.Port != 8081 {
		t.Errorf("expected 8081, got %d", cfg.Daemon.Port)
	}
}

func TestWave228_ParseForgeTOML_NonExistentFile(t *testing.T) {
	_, err := parseForgeTOML("/nonexistent/path/forge.toml")
	if err == nil {
		t.Error("expected error for non-existent file")
	}
}

func TestWave228_ParseForgeTOML_EmptyFile(t *testing.T) {
	f, err := os.CreateTemp("", "forge-empty-*.toml")
	if err != nil {
		t.Fatalf("create temp: %v", err)
	}
	defer os.Remove(f.Name())
	f.Close()

	cfg, err := parseForgeTOML(f.Name())
	if err != nil {
		t.Fatalf("parseForgeTOML empty: %v", err)
	}
	if cfg.Daemon.Port != 0 {
		t.Errorf("expected 0 port for empty config, got %d", cfg.Daemon.Port)
	}
}

// --- loadForgeConfig (non-blocking path) ---

func TestWave228_LoadForgeConfig_NoFile(t *testing.T) {
	os.Setenv("FORGE_CONFIG", "/nonexistent/forge.toml")
	defer os.Unsetenv("FORGE_CONFIG")
	// Should return zero config without panic
	cfg := loadForgeConfig()
	if cfg.Daemon.Port != 0 {
		t.Errorf("expected zero config, got port=%d", cfg.Daemon.Port)
	}
}

func TestWave228_LoadForgeConfig_WithFile(t *testing.T) {
	content := "[daemon]\nport = 7777\n"
	f, err := os.CreateTemp("", "forge-*.toml")
	if err != nil {
		t.Fatalf("create temp: %v", err)
	}
	defer os.Remove(f.Name())
	fmt.Fprint(f, content)
	f.Close()

	os.Setenv("FORGE_CONFIG", f.Name())
	defer os.Unsetenv("FORGE_CONFIG")

	cfg := loadForgeConfig()
	if cfg.Daemon.Port != 7777 {
		t.Errorf("expected port=7777, got %d", cfg.Daemon.Port)
	}
}
