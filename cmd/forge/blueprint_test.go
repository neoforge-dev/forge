package main

import "testing"

func TestBlueprintValidateDefaultConfig(t *testing.T) {
	runner := NewTestRunner(t)

	// Validate the canonical coding/default blueprint from repo config
	_, err := runner.Execute("blueprint", "validate", "config/blueprints/coding/default.yaml")
	if err != nil {
		t.Errorf("blueprint validate config/blueprints/coding/default.yaml failed: %v", err)
	}
}

func TestBlueprintValidateMissingFile(t *testing.T) {
	runner := NewTestRunner(t)

	// Validate that a missing file produces an error
	_, err := runner.Execute("blueprint", "validate", "config/blueprints/does-not-exist.yaml")
	if err == nil {
		t.Errorf("expected error for missing blueprint file, got nil")
	}
}

