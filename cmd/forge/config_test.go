// Package main provides tests for the config noun
package main

import (
	"testing"
)

func TestConfigList(t *testing.T) {
	runner := NewTestRunner(t)

	// Test basic list
	_, err := runner.Execute("config", "list")
	if err != nil {
		t.Errorf("config list failed: %v", err)
	}
}

func TestConfigListJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test with json format
	_, err := runner.Execute("config", "list", "--format", "json")
	if err != nil {
		t.Errorf("config list --format json failed: %v", err)
	}
}

func TestConfigListCsvFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test with csv format
	_, err := runner.Execute("config", "list", "--format", "csv")
	if err != nil {
		t.Errorf("config list --format csv failed: %v", err)
	}
}

func TestConfigListQuietFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test with quiet format
	_, err := runner.Execute("config", "list", "--format", "quiet")
	if err != nil {
		t.Errorf("config list --format quiet failed: %v", err)
	}
}

func TestConfigShow(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show config
	_, err := runner.Execute("config", "show")
	if err != nil {
		t.Errorf("config show failed: %v", err)
	}
}

func TestConfigShowJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with json format
	_, err := runner.Execute("config", "show", "--format", "json")
	if err != nil {
		t.Errorf("config show --format json failed: %v", err)
	}
}

func TestConfigShowQuietFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with quiet format
	_, err := runner.Execute("config", "show", "--format", "quiet")
	if err != nil {
		t.Errorf("config show --format quiet failed: %v", err)
	}
}

func TestConfigGet(t *testing.T) {
	runner := NewTestRunner(t)

	// Test get specific config value
	_, err := runner.Execute("config", "get", "forge.home")
	if err != nil {
		t.Errorf("config get failed: %v", err)
	}
}

func TestConfigGetNotFound(t *testing.T) {
	runner := NewTestRunner(t)

	// Test get non-existent key
	_, err := runner.Execute("config", "get", "nonexistent.key.xyz")
	if err != nil {
		t.Errorf("config get for nonexistent key failed: %v", err)
	}
}

func TestConfigGetJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test get with json format
	_, err := runner.Execute("config", "get", "forge.home", "--format", "json")
	if err != nil {
		t.Errorf("config get --format json failed: %v", err)
	}
}
