//go:build !tmux_bridge
// +build !tmux_bridge

package interfaces

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"
)

// ─── Mock implementations for testing ─────────────────────────────────────
//
// NOTE on Validate vs ValidatePointer:
//   - Validate(iface, impl) dereferences pointer types to get the value type,
//     then checks if the value type implements the interface.
//     → Use with VALUE receiver mocks so the struct type itself implements the interface.
//   - ValidatePointer(iface, impl) expects impl to already be a pointer and checks
//     if THAT pointer type implements the interface.
//     → Use with POINTER receiver mocks.

// valueContextManager uses VALUE receivers so the bare struct satisfies ContextManager.
type valueContextManager struct{}

func (m valueContextManager) GenerateEnvelope(_ context.Context, _, _, _, _, _ string) (*ContextEnvelope, error) {
	return &ContextEnvelope{ID: "mock"}, nil
}
func (m valueContextManager) BootstrapAgent(_ context.Context, _, _ string) (*ContextEnvelope, error) {
	return &ContextEnvelope{}, nil
}
func (m valueContextManager) SyncEnvelopesToFilesystem() error      { return nil }
func (m valueContextManager) SyncDomainToFilesystem(_ string) error { return nil }
func (m valueContextManager) Stop()                                  {}

// valueRoyalJelly uses VALUE receivers so the struct itself satisfies RoyalJelly.
type valueRoyalJelly struct{}

func (m valueRoyalJelly) RegisterHook(_ string, _ Hook)                       {}
func (m valueRoyalJelly) TriggerHooks(_ context.Context, _ HookEvent) error { return nil }

// valuePatrolSystem uses VALUE receivers for PatrolSystem interface.
type valuePatrolSystem struct{}

func (m valuePatrolSystem) SetContextManager(_ ContextManager) {}
func (m valuePatrolSystem) SetRoyalJelly(_ RoyalJelly)         {}
func (m valuePatrolSystem) Start()                              {}
func (m valuePatrolSystem) Stop()                               {}

// valueHTTPHandler uses VALUE receivers for HTTPHandler interface.
type valueHTTPHandler struct{}

func (m valueHTTPHandler) RegisterRoutes(_ *http.ServeMux) {}

// ptrContextManager uses POINTER receivers (only *ptrContextManager implements the interface).
type ptrContextManager struct{}

func (m *ptrContextManager) GenerateEnvelope(_ context.Context, _, _, _, _, _ string) (*ContextEnvelope, error) {
	return &ContextEnvelope{}, nil
}
func (m *ptrContextManager) BootstrapAgent(_ context.Context, _, _ string) (*ContextEnvelope, error) {
	return &ContextEnvelope{}, nil
}
func (m *ptrContextManager) SyncEnvelopesToFilesystem() error      { return nil }
func (m *ptrContextManager) SyncDomainToFilesystem(_ string) error { return nil }
func (m *ptrContextManager) Stop()                                  {}

// ptrRoyalJelly uses POINTER receivers for RoyalJelly interface.
type ptrRoyalJelly struct{}

func (m *ptrRoyalJelly) RegisterHook(_ string, _ Hook)                       {}
func (m *ptrRoyalJelly) TriggerHooks(_ context.Context, _ HookEvent) error { return nil }

// badType does NOT satisfy any of the interfaces above.
type badType struct{ _ int }

// ─── TestWave114 tests ─────────────────────────────────────────────────────

// TestWave114_NewValidator verifies that NewValidator returns a non-nil validator
// with no errors initially.
func TestWave114_NewValidator(t *testing.T) {
	v := NewValidator()
	if v == nil {
		t.Fatal("NewValidator returned nil")
	}
	if v.HasErrors() {
		t.Errorf("expected no errors on new validator, got: %v", v.Errors())
	}
	if v.Error() != "" {
		t.Errorf("expected empty Error() on new validator, got: %q", v.Error())
	}
	if v.ReturnError() != nil {
		t.Errorf("expected nil ReturnError() on new validator, got: %v", v.ReturnError())
	}
}

// TestWave114_Validate_PassingContextManager verifies that a value-receiver ContextManager
// implementation passes Validate (which dereferences pointer to check value type).
func TestWave114_Validate_PassingContextManager(t *testing.T) {
	v := NewValidator()
	// Passing &valueContextManager{} — Validate dereferences to valueContextManager,
	// which has value-receiver methods and satisfies ContextManager.
	v.Validate((*ContextManager)(nil), &valueContextManager{})
	if v.HasErrors() {
		t.Errorf("expected no errors for valid ContextManager impl, got: %v", v.Errors())
	}
}

// TestWave114_Validate_PassingRoyalJelly verifies value-receiver RoyalJelly passes Validate.
func TestWave114_Validate_PassingRoyalJelly(t *testing.T) {
	v := NewValidator()
	v.Validate((*RoyalJelly)(nil), &valueRoyalJelly{})
	if v.HasErrors() {
		t.Errorf("expected no errors for valid RoyalJelly impl, got: %v", v.Errors())
	}
}

// TestWave114_Validate_PassingPatrolSystem verifies value-receiver PatrolSystem passes Validate.
func TestWave114_Validate_PassingPatrolSystem(t *testing.T) {
	v := NewValidator()
	v.Validate((*PatrolSystem)(nil), &valuePatrolSystem{})
	if v.HasErrors() {
		t.Errorf("expected no errors for valid PatrolSystem impl, got: %v", v.Errors())
	}
}

// TestWave114_Validate_PassingHTTPHandler verifies value-receiver HTTPHandler passes Validate.
func TestWave114_Validate_PassingHTTPHandler(t *testing.T) {
	v := NewValidator()
	v.Validate((*HTTPHandler)(nil), &valueHTTPHandler{})
	if v.HasErrors() {
		t.Errorf("expected no errors for valid HTTPHandler impl, got: %v", v.Errors())
	}
}

// TestWave114_Validate_FailingImplementation verifies that a non-satisfying type records error.
func TestWave114_Validate_FailingImplementation(t *testing.T) {
	v := NewValidator()
	v.Validate((*ContextManager)(nil), &badType{})
	if !v.HasErrors() {
		t.Error("expected error for invalid ContextManager impl, got none")
	}
	errs := v.Errors()
	if len(errs) != 1 {
		t.Errorf("expected 1 error, got %d", len(errs))
	}
}

// TestWave114_Validate_Chaining verifies that Validate returns *InterfaceValidator for chaining.
func TestWave114_Validate_Chaining(t *testing.T) {
	v := NewValidator()
	result := v.Validate((*ContextManager)(nil), &valueContextManager{}).
		Validate((*RoyalJelly)(nil), &valueRoyalJelly{})
	if result == nil {
		t.Fatal("Validate should return non-nil *InterfaceValidator for chaining")
	}
	if result.HasErrors() {
		t.Errorf("chained validation should have no errors, got: %v", result.Errors())
	}
}

// TestWave114_Validate_MultipleErrors verifies that multiple failures accumulate errors.
func TestWave114_Validate_MultipleErrors(t *testing.T) {
	v := NewValidator()
	v.Validate((*ContextManager)(nil), &badType{})
	v.Validate((*RoyalJelly)(nil), &badType{})

	if !v.HasErrors() {
		t.Error("expected errors for two invalid impls")
	}
	errs := v.Errors()
	if len(errs) != 2 {
		t.Errorf("expected 2 errors, got %d", len(errs))
	}
}

// TestWave114_ValidatePointer_Passing verifies ValidatePointer with a pointer type
// that satisfies the interface via pointer receivers.
func TestWave114_ValidatePointer_Passing(t *testing.T) {
	v := NewValidator()
	// ptrContextManager has pointer receivers — pass (*ptrContextManager)(nil) so
	// ValidatePointer sees the *ptrContextManager type (no dereference).
	v.ValidatePointer((*ContextManager)(nil), (*ptrContextManager)(nil))
	if v.HasErrors() {
		t.Errorf("expected no errors for valid pointer implementation, got: %v", v.Errors())
	}
}

// TestWave114_ValidatePointer_PassingRoyalJelly verifies ValidatePointer for RoyalJelly.
func TestWave114_ValidatePointer_PassingRoyalJelly(t *testing.T) {
	v := NewValidator()
	v.ValidatePointer((*RoyalJelly)(nil), (*ptrRoyalJelly)(nil))
	if v.HasErrors() {
		t.Errorf("expected no errors for valid RoyalJelly pointer impl, got: %v", v.Errors())
	}
}

// TestWave114_ValidatePointer_Failing verifies ValidatePointer records error for non-satisfying type.
func TestWave114_ValidatePointer_Failing(t *testing.T) {
	v := NewValidator()
	v.ValidatePointer((*ContextManager)(nil), (*badType)(nil))
	if !v.HasErrors() {
		t.Error("expected error for invalid pointer implementation")
	}
}

// TestWave114_Error_FormatsCorrectly verifies the Error() method produces a readable message.
func TestWave114_Error_FormatsCorrectly(t *testing.T) {
	v := NewValidator()
	v.Validate((*ContextManager)(nil), &badType{})

	errStr := v.Error()
	if errStr == "" {
		t.Error("Error() should return non-empty string when errors exist")
	}
	if !strings.Contains(errStr, "Interface validation failed") {
		t.Errorf("Error() should contain 'Interface validation failed', got: %q", errStr)
	}
	if !strings.Contains(errStr, "badType") {
		t.Errorf("Error() should mention the type name 'badType', got: %q", errStr)
	}
}

// TestWave114_ReturnError_WithErrors verifies ReturnError returns non-nil error when errors exist.
func TestWave114_ReturnError_WithErrors(t *testing.T) {
	v := NewValidator()
	v.Validate((*ContextManager)(nil), &badType{})

	err := v.ReturnError()
	if err == nil {
		t.Error("ReturnError() should return non-nil error when errors exist")
	}
	if !strings.Contains(err.Error(), "Interface validation failed") {
		t.Errorf("ReturnError() error message unexpected: %q", err.Error())
	}
}

// TestWave114_ReturnError_NoErrors verifies ReturnError returns nil when no errors.
func TestWave114_ReturnError_NoErrors(t *testing.T) {
	v := NewValidator()
	v.Validate((*ContextManager)(nil), &valueContextManager{})

	err := v.ReturnError()
	if err != nil {
		t.Errorf("ReturnError() should return nil when no errors, got: %v", err)
	}
}

// TestWave114_Errors_ReturnsSlice verifies Errors() returns the accumulated slice.
func TestWave114_Errors_ReturnsSlice(t *testing.T) {
	v := NewValidator()

	// No errors initially
	errs := v.Errors()
	if len(errs) != 0 {
		t.Errorf("expected empty errors slice, got %d entries", len(errs))
	}

	// After a failing validation
	v.Validate((*ContextManager)(nil), &badType{})
	errs = v.Errors()
	if len(errs) != 1 {
		t.Errorf("expected 1 error after one failure, got %d", len(errs))
	}
}

// TestWave114_ContextEnvelope_Fields verifies ContextEnvelope struct fields are accessible.
func TestWave114_ContextEnvelope_Fields(t *testing.T) {
	now := time.Now()
	env := ContextEnvelope{
		ID:          "env-1",
		AgentID:     "agent-1",
		Domain:      "coding",
		Project:     "forge",
		TaskID:      "task-1",
		CreatedAt:   now,
		ExpiresAt:   now.Add(time.Hour),
		Summary:     "test summary",
		Decisions:   []Decision{{ID: "d1", Description: "desc", Reasoning: "reason", Timestamp: now}},
		Failures:    []Failure{{ID: "f1", Description: "fail", Lesson: "learned", Timestamp: now}},
		Calibration: map[string]string{"key": "value"},
		KeyFiles:    []string{"file1.go"},
		Metadata:    map[string]any{"meta": 1},
	}
	if env.ID != "env-1" {
		t.Errorf("expected ID 'env-1', got %q", env.ID)
	}
	if env.Summary != "test summary" {
		t.Errorf("expected Summary 'test summary', got %q", env.Summary)
	}
	if len(env.Decisions) != 1 {
		t.Errorf("expected 1 Decision, got %d", len(env.Decisions))
	}
	if len(env.Failures) != 1 {
		t.Errorf("expected 1 Failure, got %d", len(env.Failures))
	}
}

// TestWave114_HookEvent_Fields verifies HookEvent struct fields are accessible.
func TestWave114_HookEvent_Fields(t *testing.T) {
	evt := HookEvent{
		Type:       "context_critical",
		AgentID:    "agent-1",
		ContextPct: 0.85,
		Timestamp:  time.Now().Unix(),
		Metadata:   map[string]interface{}{"key": "value"},
	}
	if evt.Type != "context_critical" {
		t.Errorf("expected Type 'context_critical', got %q", evt.Type)
	}
	if evt.ContextPct != 0.85 {
		t.Errorf("expected ContextPct 0.85, got %f", evt.ContextPct)
	}
}

// TestWave114_ValidateAndChain_MixedResults verifies chaining with mixed passing/failing impls.
func TestWave114_ValidateAndChain_MixedResults(t *testing.T) {
	v := NewValidator()
	v.Validate((*ContextManager)(nil), &valueContextManager{}).
		Validate((*ContextManager)(nil), &badType{}).
		Validate((*RoyalJelly)(nil), &valueRoyalJelly{})

	if !v.HasErrors() {
		t.Error("expected errors after one bad impl in chain")
	}
	if len(v.Errors()) != 1 {
		t.Errorf("expected exactly 1 error, got %d: %v", len(v.Errors()), v.Errors())
	}
}

// TestWave114_HasErrors_FalseWhenClean verifies HasErrors returns false for a fresh validator.
func TestWave114_HasErrors_FalseWhenClean(t *testing.T) {
	v := NewValidator()
	if v.HasErrors() {
		t.Error("new validator should have no errors")
	}
}

// TestWave114_HasErrors_TrueAfterFailure verifies HasErrors returns true after a failing Validate.
func TestWave114_HasErrors_TrueAfterFailure(t *testing.T) {
	v := NewValidator()
	v.Validate((*ContextManager)(nil), &badType{})
	if !v.HasErrors() {
		t.Error("expected HasErrors() true after failing Validate")
	}
}

// TestWave114_Error_EmptyWhenNoErrors verifies Error() returns empty string when no errors.
func TestWave114_Error_EmptyWhenNoErrors(t *testing.T) {
	v := NewValidator()
	if v.Error() != "" {
		t.Errorf("expected empty Error() with no failures, got: %q", v.Error())
	}
}

// TestWave114_ValidatePointer_ChainMultiple verifies ValidatePointer chaining accumulates correctly.
func TestWave114_ValidatePointer_ChainMultiple(t *testing.T) {
	v := NewValidator()
	v.ValidatePointer((*ContextManager)(nil), (*ptrContextManager)(nil)).
		ValidatePointer((*RoyalJelly)(nil), (*ptrRoyalJelly)(nil))

	if v.HasErrors() {
		t.Errorf("expected no errors for two valid pointer impls, got: %v", v.Errors())
	}
}

// TestWave114_Validate_ValidatePointer_Mixed verifies mixing Validate and ValidatePointer calls.
func TestWave114_Validate_ValidatePointer_Mixed(t *testing.T) {
	v := NewValidator()
	v.Validate((*ContextManager)(nil), &valueContextManager{})
	v.ValidatePointer((*RoyalJelly)(nil), (*ptrRoyalJelly)(nil))
	v.Validate((*PatrolSystem)(nil), &valuePatrolSystem{})

	if v.HasErrors() {
		t.Errorf("expected no errors for all valid impls, got: %v", v.Errors())
	}
}
