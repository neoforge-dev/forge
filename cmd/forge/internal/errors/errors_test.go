package errors

import (
	"testing"
)

func TestCLIError_Error(t *testing.T) {
	e := New(ExitGeneralError, "test", "something failed")
	if e.Error() != "something failed" {
		t.Errorf("Error() = %q, want %q", e.Error(), "something failed")
	}
}

func TestNew(t *testing.T) {
	e := New(ExitNotFound, "not_found", "task xyz not found")
	if e.Code != ExitNotFound || e.Type != "not_found" || e.Message != "task xyz not found" {
		t.Errorf("New: got %+v", e)
	}
	if e.Details == nil {
		t.Error("Details map should be initialized")
	}
}

func TestWithDetail(t *testing.T) {
	e := New(ExitPermission, "denied", "access denied").WithDetail("resource", "repo")
	if e.Details["resource"] != "repo" {
		t.Errorf("WithDetail: got %v", e.Details["resource"])
	}
	e2 := e.WithDetail("action", "write")
	if e2 != e {
		t.Error("WithDetail should return same pointer")
	}
	if e.Details["action"] != "write" {
		t.Errorf("WithDetail action: got %v", e.Details["action"])
	}
}

func TestDaemonNotRunning(t *testing.T) {
	e := DaemonNotRunning()
	if e.Code != ExitDaemonOffline || e.Type != "daemon_offline" {
		t.Errorf("DaemonNotRunning: got %+v", e)
	}
	if e.Message == "" {
		t.Error("message should not be empty")
	}
}

func TestNotFound(t *testing.T) {
	e := NotFound("task", "T-001")
	if e.Code != ExitNotFound || e.Type != "not_found" {
		t.Errorf("NotFound: got %+v", e)
	}
	if e.Message != "task not found: T-001" {
		t.Errorf("message = %q", e.Message)
	}
}

func TestPermissionDenied(t *testing.T) {
	e := PermissionDenied("push")
	if e.Code != ExitPermission || e.Type != "permission_denied" {
		t.Errorf("PermissionDenied: got %+v", e)
	}
	if e.Message != "Permission denied: push" {
		t.Errorf("message = %q", e.Message)
	}
}

func TestTimeout(t *testing.T) {
	e := Timeout("sync")
	if e.Code != ExitTimeout || e.Type != "timeout" {
		t.Errorf("Timeout: got %+v", e)
	}
	if e.Message != "Operation timed out: sync" {
		t.Errorf("message = %q", e.Message)
	}
}

func TestExitConstants(t *testing.T) {
	if ExitSuccess != 0 || ExitGeneralError != 1 || ExitMisuse != 2 ||
		ExitDaemonOffline != 3 || ExitPermission != 4 || ExitNotFound != 5 || ExitTimeout != 6 {
		t.Error("exit constants changed")
	}
}
