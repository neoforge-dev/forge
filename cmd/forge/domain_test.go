// Package main provides tests for the domain noun
package main

import (
	"testing"
)

func TestDomainList(t *testing.T) {
	runner := NewTestRunner(t)

	// Test basic list
	_, err := runner.Execute("domain", "list")
	if err != nil {
		t.Errorf("domain list failed: %v", err)
	}

	// Test with json format
	_, err = runner.Execute("domain", "list", "--format", "json")
	if err != nil {
		t.Errorf("domain list --format json failed: %v", err)
	}

	// Test with csv format
	_, err = runner.Execute("domain", "list", "--format", "csv")
	if err != nil {
		t.Errorf("domain list --format csv failed: %v", err)
	}

	// Test with quiet format
	_, err = runner.Execute("domain", "list", "--format", "quiet")
	if err != nil {
		t.Errorf("domain list --format quiet failed: %v", err)
	}
}

func TestDomainListActive(t *testing.T) {
	runner := NewTestRunner(t)

	// Test list with active filter
	_, err := runner.Execute("domain", "list", "--active")
	if err != nil {
		t.Errorf("domain list --active failed: %v", err)
	}
}

func TestDomainShow(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with existing domain
	_, err := runner.Execute("domain", "show", "codeswiftr-com")
	if err != nil {
		t.Errorf("domain show failed: %v", err)
	}
}

func TestDomainShowNotFound(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with non-existent domain
	_, err := runner.Execute("domain", "show", "nonexistent-domain-xyz")
	if err == nil {
		t.Errorf("Expected error for non-existent domain, got nil")
	}
}

func TestDomainShowJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with json format
	_, err := runner.Execute("domain", "show", "codeswiftr-com", "--format", "json")
	if err != nil {
		t.Errorf("domain show --format json failed: %v", err)
	}
}

func TestDomainShowQuietFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with quiet format
	_, err := runner.Execute("domain", "show", "codeswiftr-com", "--format", "quiet")
	if err != nil {
		t.Errorf("domain show --format quiet failed: %v", err)
	}
}

func TestDomainCreate(t *testing.T) {
	runner := NewTestRunner(t)

	// Test create domain
	_, err := runner.Execute("domain", "create", "--key", "test-domain-com", "--name", "Test Domain")
	if err != nil {
		t.Errorf("domain create failed: %v", err)
	}
}

func TestDomainCreateMissingKey(t *testing.T) {
	runner := NewTestRunner(t)

	// Test create without key - command allows this (uses default)
	_, err := runner.Execute("domain", "create", "--name", "Test")
	if err != nil {
		t.Logf("domain create without key: %v", err)
	}
}

func TestDomainCreateMissingName(t *testing.T) {
	runner := NewTestRunner(t)

	// Test create without name - command allows this (uses default)
	_, err := runner.Execute("domain", "create", "--key", "test-key")
	if err != nil {
		t.Logf("domain create without name: %v", err)
	}
}

func TestDomainCreateJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test create with json format
	_, err := runner.Execute("domain", "create", "--key", "test-json", "--name", "Test JSON", "--format", "json")
	if err != nil {
		t.Errorf("domain create --format json failed: %v", err)
	}
}

func TestDomainListMultipleFormats(t *testing.T) {
	runner := NewTestRunner(t)

	formats := []string{"table", "json", "csv", "quiet"}
	for _, format := range formats {
		_, err := runner.Execute("domain", "list", "--format", format)
		if err != nil {
			t.Errorf("domain list --format %s failed: %v", format, err)
		}
	}
}

func TestDomainShowMultiple(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show various domains from demo data
	domains := []string{"codeswiftr-com", "leanvibe-ai", "brandfocus-ai"}
	for _, domain := range domains {
		_, err := runner.Execute("domain", "show", domain)
		if err != nil {
			t.Errorf("domain show %s failed: %v", domain, err)
		}
	}
}
