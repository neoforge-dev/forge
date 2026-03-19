// Package main provides tests for the pattern noun
package main

import (
	"testing"
)

func TestPatternList(t *testing.T) {
	runner := NewTestRunner(t)

	// Test basic list
	_, err := runner.Execute("pattern", "list")
	if err != nil {
		t.Errorf("pattern list failed: %v", err)
	}
}

func TestPatternListJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test with json format
	_, err := runner.Execute("pattern", "list", "--format", "json")
	if err != nil {
		t.Errorf("pattern list --format json failed: %v", err)
	}
}

func TestPatternListCsvFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test with csv format
	_, err := runner.Execute("pattern", "list", "--format", "csv")
	if err != nil {
		t.Errorf("pattern list --format csv failed: %v", err)
	}
}

func TestPatternListQuietFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test with quiet format
	_, err := runner.Execute("pattern", "list", "--format", "quiet")
	if err != nil {
		t.Errorf("pattern list --format quiet failed: %v", err)
	}
}

func TestPatternShow(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show pattern
	_, err := runner.Execute("pattern", "show", "api_endpoint:simple")
	if err != nil {
		t.Errorf("pattern show failed: %v", err)
	}
}

func TestPatternShowNotFound(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show non-existent pattern
	_, err := runner.Execute("pattern", "show", "nonexistent-pattern-xyz")
	if err == nil {
		t.Errorf("Expected error for non-existent pattern, got nil")
	}
}

func TestPatternShowJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with json format
	_, err := runner.Execute("pattern", "show", "api_endpoint:simple", "--format", "json")
	if err != nil {
		t.Errorf("pattern show --format json failed: %v", err)
	}
}

func TestPatternShowQuietFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with quiet format
	_, err := runner.Execute("pattern", "show", "api_endpoint:simple", "--format", "quiet")
	if err != nil {
		t.Errorf("pattern show --format quiet failed: %v", err)
	}
}

func TestPatternSearch(t *testing.T) {
	runner := NewTestRunner(t)

	// Test search patterns
	_, err := runner.Execute("pattern", "search", "fastapi")
	if err != nil {
		t.Errorf("pattern search failed: %v", err)
	}
}

func TestPatternSearchJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test search with json format
	_, err := runner.Execute("pattern", "search", "api", "--format", "json")
	if err != nil {
		t.Errorf("pattern search --format json failed: %v", err)
	}
}

func TestPatternSearchNoResults(t *testing.T) {
	runner := NewTestRunner(t)

	// Test search with no results
	_, err := runner.Execute("pattern", "search", "xyznonexistent123")
	if err != nil {
		t.Errorf("pattern search no results failed: %v", err)
	}
}

func TestPatternCategories(t *testing.T) {
	runner := NewTestRunner(t)

	// Test list with category filter
	_, err := runner.Execute("pattern", "list", "--category", "code")
	if err != nil {
		t.Errorf("pattern list --category failed: %v", err)
	}
}

func TestPatternTags(t *testing.T) {
	runner := NewTestRunner(t)

	// Test list with tag filter
	_, err := runner.Execute("pattern", "list", "--tag", "python")
	if err != nil {
		t.Errorf("pattern list --tag failed: %v", err)
	}
}
