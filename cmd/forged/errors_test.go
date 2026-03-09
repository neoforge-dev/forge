//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"fmt"
	"os"
	"strings"
	"testing"
)

func TestForgeErrorErrorStringIncludesSuggestion(t *testing.T) {
	err := &ForgeError{
		Code:       ErrCodeValidation,
		Message:    "invalid",
		Suggestion: "do this",
	}

	got := err.Error()
	if !strings.Contains(got, "Suggestion:") {
		t.Fatalf("Error() = %q, expected to contain 'Suggestion:'", got)
	}
}

func TestForgeErrorExitCodeMapping(t *testing.T) {
	tests := []struct {
		code ErrorCode
		exit int
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
		{"UNKNOWN", ExitGeneralError},
	}

	for _, tt := range tests {
		t.Run(string(tt.code), func(t *testing.T) {
			err := &ForgeError{Code: tt.code}
			if got := err.ExitCode(); got != tt.exit {
				t.Fatalf("ExitCode(%s) = %d, want %d", tt.code, got, tt.exit)
			}
		})
	}
}

func TestIsForgeError(t *testing.T) {
	base := &ForgeError{Code: ErrCodeInternal}
	if fe, ok := IsForgeError(base); !ok || fe != base {
		t.Fatalf("IsForgeError should recognize ForgeError")
	}

	if _, ok := IsForgeError(nil); ok {
		t.Fatalf("IsForgeError(nil) should be false")
	}
}

func TestErrorConstructors(t *testing.T) {
	errs := []struct {
		name string
		err  error
		want ErrorCode
	}{
		{"not found", NewNotFoundError("task", "123"), ErrCodeNotFound},
		{"invalid state", NewInvalidStateError("TASK-1", "running", "completed"), ErrCodeInvalidState},
		{"unauthorized", NewUnauthorizedError("do something"), ErrCodeUnauthorized},
		{"lease expired", NewLeaseExpiredError("TASK-1"), ErrCodeLeaseExpired},
		{"duplicate", NewDuplicateError("task", "123"), ErrCodeDuplicate},
		{"timeout", NewTimeoutError("op", "1s"), ErrCodeTimeout},
		{"validation", NewValidationError("field", "reason"), ErrCodeValidation},
		{"internal", NewInternalError("details"), ErrCodeInternal},
		{"unavailable", NewUnavailableError("service"), ErrCodeUnavailable},
	}

	for _, tt := range errs {
		t.Run(tt.name, func(t *testing.T) {
			fe, ok := IsForgeError(tt.err)
			if !ok {
				t.Fatalf("constructor did not return ForgeError, got %T", tt.err)
			}
			if fe.Code != tt.want {
				t.Fatalf("ForgeError.Code = %s, want %s", fe.Code, tt.want)
			}
			if fe.Error() == "" {
				t.Fatalf("ForgeError.Error() must not be empty")
			}
			if fe.JSON() == "" {
				t.Fatalf("ForgeError.JSON() must not be empty")
			}
		})
	}
}

// TestForgeError_Methods_W23B tests ForgeError methods
func TestForgeError_Methods_W23B(t *testing.T) {
	err := NewNotFoundError("task", "TASK-123")
	fe, ok := IsForgeError(err)
	if !ok {
		t.Fatal("expected ForgeError")
	}
	if fe.Error() == "" {
		t.Error("Error() should not be empty")
	}
	if fe.JSON() == "" {
		t.Error("JSON() should not be empty")
	}
	if fe.ExitCode() == 0 {
		t.Error("ExitCode() should be non-zero")
	}
}

// TestForgeError_Constructors_W23B tests error constructors
func TestForgeError_Constructors_W23B(t *testing.T) {
	tests := []struct {
		name string
		err  error
	}{
		{"not found", NewNotFoundError("task", "123")},
		{"invalid state", NewInvalidStateError("123", "QUEUED", "RUNNING")},
		{"unauthorized", NewUnauthorizedError("claim")},
		{"lease expired", NewLeaseExpiredError("123")},
		{"duplicate", NewDuplicateError("task", "123")},
		{"timeout", NewTimeoutError("claim", "30s")},
		{"validation", NewValidationError("field", "required")},
		{"internal", NewInternalError("details")},
		{"unavailable", NewUnavailableError("service")},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if tt.err == nil {
				t.Fatal("expected non-nil error")
			}
			fe, ok := IsForgeError(tt.err)
			if !ok {
				t.Fatalf("expected ForgeError, got %T", tt.err)
			}
			_ = fe.Error()
			_ = fe.JSON()
			_ = fe.ExitCode()
		})
	}
}

// TestForgeError_ExitWithError_W23B tests ExitWithError (skip - calls os.Exit)
func TestForgeError_ExitWithError_W23B(t *testing.T) {
	t.Skip("ExitWithError calls os.Exit - tested indirectly via other tests")
}

// TestForgeError_ExitWithJSON_W23B tests ExitWithJSON (skip - calls os.Exit)
func TestForgeError_ExitWithJSON_W23B(t *testing.T) {
	t.Skip("ExitWithJSON calls os.Exit - tested indirectly via other tests")
}

// TestForgeError_IsForgeError_Negative_W23B tests IsForgeError with non-ForgeError
func TestForgeError_IsForgeError_Negative_W23B(t *testing.T) {
	_, ok := IsForgeError(nil)
	if ok {
		t.Error("expected false for nil")
	}
	_, ok = IsForgeError(struct{ error }{})
	if ok {
		t.Error("expected false for non-ForgeError")
	}
}

// TestWave66_ExitWithError_ForgeError_Subprocess tests ExitWithError using subprocess
// to capture the os.Exit call without terminating the test process.
func TestWave66_ExitWithError_ForgeError_Subprocess(t *testing.T) {
	if os.Getenv("FORGE_TEST_EXIT_HELPER") == "1" {
		// Running inside subprocess — call the function and let it exit.
		err := NewNotFoundError("task", "TASK-999")
		ExitWithError(err)
		return
	}
	cmd := os.Args[0]
	// Re-run this specific test in a subprocess.
	proc, err := os.StartProcess(cmd, []string{cmd, "-test.run=TestWave66_ExitWithError_ForgeError_Subprocess", "-test.v"}, &os.ProcAttr{
		Env: append(os.Environ(), "FORGE_TEST_EXIT_HELPER=1"),
	})
	if err != nil {
		t.Skipf("subprocess unavailable: %v", err)
	}
	state, err := proc.Wait()
	if err != nil {
		t.Logf("subprocess wait error: %v", err)
	}
	// ExitCode for NOT_FOUND is ExitNotFound == 2.
	if state.ExitCode() != ExitNotFound {
		t.Errorf("expected exit code %d (ExitNotFound), got %d", ExitNotFound, state.ExitCode())
	}
}

// TestWave66_ExitWithError_GenericError_Subprocess tests ExitWithError with a non-ForgeError.
func TestWave66_ExitWithError_GenericError_Subprocess(t *testing.T) {
	if os.Getenv("FORGE_TEST_GENERIC_EXIT_HELPER") == "1" {
		ExitWithError(fmt.Errorf("some generic error"))
		return
	}
	cmd := os.Args[0]
	proc, err := os.StartProcess(cmd, []string{cmd, "-test.run=TestWave66_ExitWithError_GenericError_Subprocess", "-test.v"}, &os.ProcAttr{
		Env: append(os.Environ(), "FORGE_TEST_GENERIC_EXIT_HELPER=1"),
	})
	if err != nil {
		t.Skipf("subprocess unavailable: %v", err)
	}
	state, err := proc.Wait()
	if err != nil {
		t.Logf("subprocess wait error: %v", err)
	}
	if state.ExitCode() != ExitGeneralError {
		t.Errorf("expected exit code %d (ExitGeneralError), got %d", ExitGeneralError, state.ExitCode())
	}
}

// TestWave66_ExitWithJSON_ForgeError_Subprocess tests ExitWithJSON using subprocess.
func TestWave66_ExitWithJSON_ForgeError_Subprocess(t *testing.T) {
	if os.Getenv("FORGE_TEST_JSON_EXIT_HELPER") == "1" {
		err := NewInternalError("subprocess test")
		ExitWithJSON(err)
		return
	}
	cmd := os.Args[0]
	proc, err := os.StartProcess(cmd, []string{cmd, "-test.run=TestWave66_ExitWithJSON_ForgeError_Subprocess", "-test.v"}, &os.ProcAttr{
		Env: append(os.Environ(), "FORGE_TEST_JSON_EXIT_HELPER=1"),
	})
	if err != nil {
		t.Skipf("subprocess unavailable: %v", err)
	}
	state, err := proc.Wait()
	if err != nil {
		t.Logf("subprocess wait error: %v", err)
	}
	if state.ExitCode() != ExitInternal {
		t.Errorf("expected exit code %d (ExitInternal), got %d", ExitInternal, state.ExitCode())
	}
}

// TestWave66_ExitWithJSON_GenericError_Subprocess tests ExitWithJSON with a non-ForgeError.
func TestWave66_ExitWithJSON_GenericError_Subprocess(t *testing.T) {
	if os.Getenv("FORGE_TEST_JSON_GENERIC_EXIT_HELPER") == "1" {
		ExitWithJSON(fmt.Errorf("generic json error"))
		return
	}
	cmd := os.Args[0]
	proc, err := os.StartProcess(cmd, []string{cmd, "-test.run=TestWave66_ExitWithJSON_GenericError_Subprocess", "-test.v"}, &os.ProcAttr{
		Env: append(os.Environ(), "FORGE_TEST_JSON_GENERIC_EXIT_HELPER=1"),
	})
	if err != nil {
		t.Skipf("subprocess unavailable: %v", err)
	}
	state, err := proc.Wait()
	if err != nil {
		t.Logf("subprocess wait error: %v", err)
	}
	if state.ExitCode() != ExitGeneralError {
		t.Errorf("expected exit code %d (ExitGeneralError), got %d", ExitGeneralError, state.ExitCode())
	}
}
