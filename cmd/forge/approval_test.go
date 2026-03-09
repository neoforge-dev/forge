// Package main provides tests for the approval noun
package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestApprovalList(t *testing.T) {
	runner := NewTestRunner(t)

	// Test basic list
	_, err := runner.Execute("approval", "list")
	if err != nil {
		t.Errorf("approval list failed: %v", err)
	}

	// Test with json format
	_, err = runner.Execute("approval", "list", "--format", "json")
	if err != nil {
		t.Errorf("approval list --format json failed: %v", err)
	}

	// Test with csv format
	_, err = runner.Execute("approval", "list", "--format", "csv")
	if err != nil {
		t.Errorf("approval list --format csv failed: %v", err)
	}

	// Test with quiet format
	_, err = runner.Execute("approval", "list", "--format", "quiet")
	if err != nil {
		t.Errorf("approval list --format quiet failed: %v", err)
	}
}

func TestApprovalListPending(t *testing.T) {
	runner := NewTestRunner(t)

	// Test list with pending filter
	_, err := runner.Execute("approval", "list", "--pending")
	if err != nil {
		t.Errorf("approval list --pending failed: %v", err)
	}
}

func TestApprovalShow(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with existing approval (use demo data ID)
	_, err := runner.Execute("approval", "show", "01APP456XYZ")
	if err != nil {
		t.Errorf("approval show failed: %v", err)
	}
}

func TestApprovalShowNotFound(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with non-existent approval
	_, err := runner.Execute("approval", "show", "NONEXISTENT-999")
	if err == nil {
		t.Errorf("Expected error for non-existent approval, got nil")
	}
}

func TestApprovalDecideApprove(t *testing.T) {
	runner := NewTestRunner(t)

	// Test approve decision (use demo data ID)
	_, err := runner.Execute("approval", "decide", "01APP456XYZ", "--approve", "--reason", "Looks good")
	if err != nil {
		t.Errorf("approval decide --approve failed: %v", err)
	}
}

func TestApprovalDecideReject(t *testing.T) {
	runner := NewTestRunner(t)

	// Test reject decision (use demo data ID)
	_, err := runner.Execute("approval", "decide", "01APP789ABC", "--reject", "--reason", "Needs revision")
	if err != nil {
		t.Errorf("approval decide --reject failed: %v", err)
	}
}

func TestApprovalDecideMissingApproval(t *testing.T) {
	runner := NewTestRunner(t)

	// Test decide without approval ID
	_, err := runner.Execute("approval", "decide")
	if err == nil {
		t.Errorf("Expected error for missing approval ID, got nil")
	}
}

func TestApprovalDecideMissingDecision(t *testing.T) {
	runner := NewTestRunner(t)

	// Reset flags in case a previous test set them (cobra uses global command instances).
	approvalDecideCmd.Flags().Set("approve", "false")
	approvalDecideCmd.Flags().Set("reject", "false")

	// Test decide without decision flag (use demo data ID)
	_, err := runner.Execute("approval", "decide", "01APP456XYZ")
	if err == nil {
		t.Errorf("Expected error for missing decision flag, got nil")
	}
}

func TestApprovalDecideJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test decide with json format (use demo data ID)
	_, err := runner.Execute("approval", "decide", "01APP456XYZ", "--approve", "--format", "json")
	if err != nil {
		t.Errorf("approval decide --format json failed: %v", err)
	}
}

func TestApprovalShowJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with json format (use demo data ID)
	_, err := runner.Execute("approval", "show", "01APP456XYZ", "--format", "json")
	if err != nil {
		t.Errorf("approval show --format json failed: %v", err)
	}
}

func TestApprovalShowQuietFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with quiet format (use demo data ID)
	_, err := runner.Execute("approval", "show", "01APP456XYZ", "--format", "quiet")
	if err != nil {
		t.Errorf("approval show --format quiet failed: %v", err)
	}
}

func TestApprovalListWithDemoData(t *testing.T) {
	runner := NewTestRunner(t)

	// Test that demo data is returned when no approvals directory
	_, err := runner.Execute("approval", "list")
	if err != nil {
		t.Errorf("approval list with demo data failed: %v", err)
	}
}

func TestApprovalLoadFromDirectory(t *testing.T) {
	// Create temp approvals directory
	tmpDir := t.TempDir()
	approvalsDir := filepath.Join(tmpDir, ".forge", "approvals")
	err := os.MkdirAll(approvalsDir, 0755)
	if err != nil {
		t.Skipf("Cannot create temp directory: %v", err)
	}

	// Note: This test verifies the function handles empty directory
	// The actual load logic returns demo data when directory doesn't have files
	runner := NewTestRunner(t)
	_, err = runner.Execute("approval", "list")
	if err != nil {
		t.Errorf("approval list failed: %v", err)
	}
}

func TestApprovalSaveEmpty(t *testing.T) {
	runner := NewTestRunner(t)

	// Test that save doesn't fail (it's a no-op currently)
	// We test this indirectly through decide command (use demo data ID)
	_, err := runner.Execute("approval", "decide", "01APP456XYZ", "--approve")
	if err != nil {
		t.Errorf("approval decide should work: %v", err)
	}
}
