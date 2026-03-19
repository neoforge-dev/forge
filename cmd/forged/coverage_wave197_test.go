//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"fmt"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 197: errors.go pure functions + task_store.go NewTaskStore
// Targets:
//   ForgeError.Error      (errors.go:48)  — with/without suggestion
//   ForgeError.JSON       (errors.go:56)  — returns JSON
//   ForgeError.ExitCode   (errors.go:62)  — all error codes
//   IsForgeError          (errors.go:88)  — is ForgeError / is not
//   NewNotFoundError      (errors.go:97)
//   NewInvalidStateError  (errors.go:105)
//   NewUnauthorizedError  (errors.go:114)
//   NewLeaseExpiredError  (errors.go:122)
//   NewDuplicateError     (errors.go:131)
//   NewTimeoutError       (errors.go:139)
//   NewValidationError    (errors.go:147)
//   NewInternalError      (errors.go:155)
//   NewUnavailableError   (errors.go:164)
//   NewTaskStore          (task_store.go:30) — non-nil
//   GetTasksByState       (task_store.go:35) — empty
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// ForgeError.Error
// ---------------------------------------------------------------------------

func TestWave197_ForgeError_Error_WithoutSuggestion(t *testing.T) {
	e := &ForgeError{Code: ErrCodeNotFound, Message: "thing not found"}
	if !strings.Contains(e.Error(), "NOT_FOUND") {
		t.Errorf("expected NOT_FOUND in error, got %q", e.Error())
	}
}

func TestWave197_ForgeError_Error_WithSuggestion(t *testing.T) {
	e := &ForgeError{Code: ErrCodeNotFound, Message: "thing not found", Suggestion: "create it"}
	if !strings.Contains(e.Error(), "Suggestion") {
		t.Errorf("expected Suggestion in error, got %q", e.Error())
	}
}

// ---------------------------------------------------------------------------
// ForgeError.JSON
// ---------------------------------------------------------------------------

func TestWave197_ForgeError_JSON_ReturnsJSON(t *testing.T) {
	e := &ForgeError{Code: ErrCodeInternal, Message: "internal error", Details: "stack trace"}
	j := e.JSON()
	if !strings.Contains(j, "INTERNAL_ERROR") {
		t.Errorf("expected INTERNAL_ERROR in JSON, got %q", j)
	}
}

// ---------------------------------------------------------------------------
// ForgeError.ExitCode
// ---------------------------------------------------------------------------

func TestWave197_ForgeError_ExitCode_AllCodes(t *testing.T) {
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
		{"UNKNOWN_CODE", ExitGeneralError},
	}
	for _, c := range cases {
		e := &ForgeError{Code: c.code}
		if got := e.ExitCode(); got != c.want {
			t.Errorf("ExitCode(%s): expected %d, got %d", c.code, c.want, got)
		}
	}
}

// ---------------------------------------------------------------------------
// IsForgeError
// ---------------------------------------------------------------------------

func TestWave197_IsForgeError_IsForgeError(t *testing.T) {
	fe := &ForgeError{Code: ErrCodeNotFound, Message: "not found"}
	result, ok := IsForgeError(fe)
	if !ok || result == nil {
		t.Error("expected IsForgeError to return true for *ForgeError")
	}
}

func TestWave197_IsForgeError_IsNotForgeError(t *testing.T) {
	_, ok := IsForgeError(fmt.Errorf("ordinary error"))
	if ok {
		t.Error("expected IsForgeError to return false for regular error")
	}
}

// ---------------------------------------------------------------------------
// Helper constructors
// ---------------------------------------------------------------------------

func TestWave197_NewNotFoundError(t *testing.T) {
	err := NewNotFoundError("task", "task-1")
	if err == nil {
		t.Error("expected non-nil error")
	}
	if fe, ok := IsForgeError(err); !ok || fe.Code != ErrCodeNotFound {
		t.Errorf("expected NOT_FOUND error, got %v", err)
	}
}

func TestWave197_NewInvalidStateError(t *testing.T) {
	err := NewInvalidStateError("task-1", "queued", "assigned")
	if fe, ok := IsForgeError(err); !ok || fe.Code != ErrCodeInvalidState {
		t.Errorf("expected INVALID_STATE, got %v", err)
	}
}

func TestWave197_NewUnauthorizedError(t *testing.T) {
	err := NewUnauthorizedError("delete task")
	if fe, ok := IsForgeError(err); !ok || fe.Code != ErrCodeUnauthorized {
		t.Errorf("expected UNAUTHORIZED, got %v", err)
	}
}

func TestWave197_NewLeaseExpiredError(t *testing.T) {
	err := NewLeaseExpiredError("task-1")
	if fe, ok := IsForgeError(err); !ok || fe.Code != ErrCodeLeaseExpired {
		t.Errorf("expected LEASE_EXPIRED, got %v", err)
	}
}

func TestWave197_NewDuplicateError(t *testing.T) {
	err := NewDuplicateError("task", "task-1")
	if fe, ok := IsForgeError(err); !ok || fe.Code != ErrCodeDuplicate {
		t.Errorf("expected DUPLICATE, got %v", err)
	}
}

func TestWave197_NewTimeoutError(t *testing.T) {
	err := NewTimeoutError("claim", "30s")
	if fe, ok := IsForgeError(err); !ok || fe.Code != ErrCodeTimeout {
		t.Errorf("expected TIMEOUT, got %v", err)
	}
}

func TestWave197_NewValidationError(t *testing.T) {
	err := NewValidationError("priority", "must be 1-10")
	if fe, ok := IsForgeError(err); !ok || fe.Code != ErrCodeValidation {
		t.Errorf("expected VALIDATION_ERROR, got %v", err)
	}
}

func TestWave197_NewInternalError(t *testing.T) {
	err := NewInternalError("stack trace here")
	if fe, ok := IsForgeError(err); !ok || fe.Code != ErrCodeInternal {
		t.Errorf("expected INTERNAL_ERROR, got %v", err)
	}
}

func TestWave197_NewUnavailableError(t *testing.T) {
	err := NewUnavailableError("database")
	if fe, ok := IsForgeError(err); !ok || fe.Code != ErrCodeUnavailable {
		t.Errorf("expected SERVICE_UNAVAILABLE, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// NewTaskStore + GetTasksByState
// ---------------------------------------------------------------------------

func TestWave197_NewTaskStore_NotNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	if ts == nil {
		t.Error("expected non-nil TaskStore")
	}
}

func TestWave197_TaskStore_GetTasksByState_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	tasks, err := ts.GetTasksByState(StateQueued)
	if err != nil {
		t.Errorf("GetTasksByState: %v", err)
	}
	// nil slice is fine for empty result
	_ = tasks
}
