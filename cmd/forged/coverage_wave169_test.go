//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"errors"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 169: errors.go — ForgeError + constructors + helpers
// Targets:
//   ForgeError.Error()          (errors.go:48) — with/without suggestion
//   ForgeError.JSON()           (errors.go:56) — returns JSON string
//   ForgeError.ExitCode()       (errors.go:62) — all error codes → exit codes
//   IsForgeError()              (errors.go:88) — ForgeError / plain error
//   NewNotFoundError            (errors.go:97)
//   NewInvalidStateError        (errors.go:105)
//   NewUnauthorizedError        (errors.go:114)
//   NewLeaseExpiredError        (errors.go:122)
//   NewDuplicateError           (errors.go:131)
//   NewTimeoutError             (errors.go:139)
//   NewValidationError          (errors.go:147)
//   NewInternalError            (errors.go:155)
//   NewUnavailableError         (errors.go:164)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// ForgeError.Error
// ---------------------------------------------------------------------------

func TestWave169_ForgeError_Error_WithSuggestion(t *testing.T) {
	e := &ForgeError{
		Code:       ErrCodeNotFound,
		Message:    "task not found",
		Suggestion: "check the task ID",
	}
	got := e.Error()
	if !strings.Contains(got, "task not found") || !strings.Contains(got, "check the task ID") {
		t.Errorf("unexpected Error() output: %q", got)
	}
}

func TestWave169_ForgeError_Error_NoSuggestion(t *testing.T) {
	e := &ForgeError{
		Code:    ErrCodeInternal,
		Message: "internal error",
	}
	got := e.Error()
	if !strings.Contains(got, "internal error") {
		t.Errorf("unexpected Error() output: %q", got)
	}
	if strings.Contains(got, "Suggestion") {
		t.Error("should not include Suggestion when empty")
	}
}

// ---------------------------------------------------------------------------
// ForgeError.JSON
// ---------------------------------------------------------------------------

func TestWave169_ForgeError_JSON(t *testing.T) {
	e := &ForgeError{
		Code:    ErrCodeNotFound,
		Message: "task missing",
	}
	got := e.JSON()
	if !strings.Contains(got, "NOT_FOUND") || !strings.Contains(got, "task missing") {
		t.Errorf("unexpected JSON output: %q", got)
	}
}

// ---------------------------------------------------------------------------
// ForgeError.ExitCode
// ---------------------------------------------------------------------------

func TestWave169_ForgeError_ExitCode_AllCodes(t *testing.T) {
	cases := []struct {
		code ErrorCode
		want int
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
		{"UNKNOWN_CODE", ExitGeneralError}, // default
	}
	for _, tc := range cases {
		e := &ForgeError{Code: tc.code}
		if got := e.ExitCode(); got != tc.want {
			t.Errorf("ExitCode(%s) = %d, want %d", tc.code, got, tc.want)
		}
	}
}

// ---------------------------------------------------------------------------
// IsForgeError
// ---------------------------------------------------------------------------

func TestWave169_IsForgeError_IsForge(t *testing.T) {
	fe := &ForgeError{Code: ErrCodeNotFound, Message: "not found"}
	result, ok := IsForgeError(fe)
	if !ok {
		t.Error("expected true for ForgeError")
	}
	if result.Code != ErrCodeNotFound {
		t.Errorf("unexpected code: %v", result.Code)
	}
}

func TestWave169_IsForgeError_PlainError(t *testing.T) {
	plain := errors.New("plain error")
	_, ok := IsForgeError(plain)
	if ok {
		t.Error("expected false for plain error")
	}
}

// ---------------------------------------------------------------------------
// Error constructors
// ---------------------------------------------------------------------------

func TestWave169_NewNotFoundError(t *testing.T) {
	err := NewNotFoundError("task", "task-123")
	fe, ok := IsForgeError(err)
	if !ok {
		t.Fatal("expected ForgeError")
	}
	if fe.Code != ErrCodeNotFound {
		t.Errorf("expected NOT_FOUND, got %s", fe.Code)
	}
	if !strings.Contains(fe.Message, "task-123") {
		t.Errorf("expected task ID in message: %q", fe.Message)
	}
}

func TestWave169_NewInvalidStateError(t *testing.T) {
	err := NewInvalidStateError("task-1", "RUNNING", "QUEUED")
	fe, ok := IsForgeError(err)
	if !ok {
		t.Fatal("expected ForgeError")
	}
	if fe.Code != ErrCodeInvalidState {
		t.Errorf("expected INVALID_STATE, got %s", fe.Code)
	}
	if fe.TaskID != "task-1" {
		t.Errorf("expected TaskID 'task-1', got %q", fe.TaskID)
	}
}

func TestWave169_NewUnauthorizedError(t *testing.T) {
	err := NewUnauthorizedError("delete tasks")
	fe, ok := IsForgeError(err)
	if !ok {
		t.Fatal("expected ForgeError")
	}
	if fe.Code != ErrCodeUnauthorized {
		t.Errorf("expected UNAUTHORIZED, got %s", fe.Code)
	}
}

func TestWave169_NewLeaseExpiredError(t *testing.T) {
	err := NewLeaseExpiredError("task-99")
	fe, ok := IsForgeError(err)
	if !ok {
		t.Fatal("expected ForgeError")
	}
	if fe.Code != ErrCodeLeaseExpired {
		t.Errorf("expected LEASE_EXPIRED, got %s", fe.Code)
	}
	if fe.TaskID != "task-99" {
		t.Errorf("expected TaskID 'task-99', got %q", fe.TaskID)
	}
}

func TestWave169_NewDuplicateError(t *testing.T) {
	err := NewDuplicateError("agent", "kimi-1")
	fe, ok := IsForgeError(err)
	if !ok {
		t.Fatal("expected ForgeError")
	}
	if fe.Code != ErrCodeDuplicate {
		t.Errorf("expected DUPLICATE, got %s", fe.Code)
	}
}

func TestWave169_NewTimeoutError(t *testing.T) {
	err := NewTimeoutError("claim task", "30s")
	fe, ok := IsForgeError(err)
	if !ok {
		t.Fatal("expected ForgeError")
	}
	if fe.Code != ErrCodeTimeout {
		t.Errorf("expected TIMEOUT, got %s", fe.Code)
	}
	if !strings.Contains(fe.Message, "30s") {
		t.Errorf("expected duration in message: %q", fe.Message)
	}
}

func TestWave169_NewValidationError(t *testing.T) {
	err := NewValidationError("priority", "must be 1-10")
	fe, ok := IsForgeError(err)
	if !ok {
		t.Fatal("expected ForgeError")
	}
	if fe.Code != ErrCodeValidation {
		t.Errorf("expected VALIDATION_ERROR, got %s", fe.Code)
	}
}

func TestWave169_NewInternalError(t *testing.T) {
	err := NewInternalError("nil pointer at line 42")
	fe, ok := IsForgeError(err)
	if !ok {
		t.Fatal("expected ForgeError")
	}
	if fe.Code != ErrCodeInternal {
		t.Errorf("expected INTERNAL_ERROR, got %s", fe.Code)
	}
	if !strings.Contains(fe.Details, "nil pointer") {
		t.Errorf("expected details in message: %q", fe.Details)
	}
}

func TestWave169_NewUnavailableError(t *testing.T) {
	err := NewUnavailableError("forged daemon")
	fe, ok := IsForgeError(err)
	if !ok {
		t.Fatal("expected ForgeError")
	}
	if fe.Code != ErrCodeUnavailable {
		t.Errorf("expected SERVICE_UNAVAILABLE, got %s", fe.Code)
	}
}
