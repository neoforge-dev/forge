package main

import (
	"testing"
)

// TestProjectListArgs tests that project list works
func TestProjectListArgs(t *testing.T) {
	runner := NewTestRunner(t)

	// Test basic list - may fail due to API, but should not crash
	_, err := runner.Execute("project", "list")
	if err != nil {
		t.Logf("project list: %v (expected if no API)", err)
	}
}

// TestProjectShowArgs tests that show requires arguments
func TestProjectShowArgs(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("project", "show")
	if err == nil {
		t.Error("expected error for missing project key")
	}
}

// TestProjectCreateArgs tests that create requires arguments
func TestProjectCreateArgs(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("project", "create")
	if err == nil {
		t.Error("expected error for missing args")
	}
}

// TestProjectCreateMissingName tests create without name
func TestProjectCreateMissingName(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("project", "create", "--domain", "codeswiftr-com")
	if err == nil {
		t.Error("expected error for missing name")
	}
}

// TestProjectCreateMissingDomain tests create without domain
func TestProjectCreateMissingDomain(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("project", "create", "--name", "Test Project")
	if err == nil {
		t.Error("expected error for missing domain")
	}
}

// TestProjectShowNotFound tests show with nonexistent project
func TestProjectShowNotFound(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("project", "show", "nonexistent/domain-xyz")
	if err == nil {
		t.Error("expected error for non-existent project")
	}
}
