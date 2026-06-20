// Package main provides tests for the approval noun
package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/neoforge-dev/forge/internal"
)

func useLocalApprovalStore(t *testing.T) {
	t.Helper()
	tmpDir := t.TempDir()
	origDir, err := os.Getwd()
	if err != nil {
		t.Fatalf("get cwd: %v", err)
	}
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("chdir temp approval store: %v", err)
	}
	t.Cleanup(func() {
		if err := os.Chdir(origDir); err != nil {
			t.Fatalf("restore cwd: %v", err)
		}
	})
	t.Setenv("FORGE_API_URL", "http://127.0.0.1:1")
	t.Setenv("FORGE_NO_RETRY", "1")
}

// writeTestApprovals creates a .forge/approvals/approvals.json with the given
// approvals in the current working directory (which tests chdir to a temp dir).
func writeTestApprovals(t *testing.T, approvals []internal.Approval) {
	t.Helper()
	dir := filepath.Join(".forge", "approvals")
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatalf("create approvals dir: %v", err)
	}
	data, err := json.MarshalIndent(approvals, "", "  ")
	if err != nil {
		t.Fatalf("marshal approvals: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "approvals.json"), data, 0644); err != nil {
		t.Fatalf("write approvals: %v", err)
	}
}

// testApprovals is a small set of real approval records used across tests.
var testApprovals = []internal.Approval{
	{
		ID:          "01APP456XYZ",
		Type:        "merge",
		Tier:        "phone",
		Domain:      "codeswiftr-com",
		Project:     "interview-simulator",
		Title:       "Merge PR #42: Add OAuth2 login",
		Confidence:  0.82,
		RiskScore:   0.15,
		Status:      "pending",
		RequestedBy: "kimi",
		RequestedAt: time.Now().Add(-2 * time.Hour),
		ExpiresAt:   time.Now().Add(22 * time.Hour),
	},
	{
		ID:          "01APP789ABC",
		Type:        "deploy",
		Tier:        "watch",
		Domain:      "brandfocus-ai",
		Project:     "voice-coach",
		Title:       "Deploy to staging",
		Confidence:  0.96,
		RiskScore:   0.05,
		Status:      "pending",
		RequestedBy: "minimax",
		RequestedAt: time.Now().Add(-30 * time.Minute),
		ExpiresAt:   time.Now().Add(23 * time.Hour),
	},
}

func TestApprovalList(t *testing.T) {
	useLocalApprovalStore(t)

	writeTestApprovals(t, testApprovals)
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
	useLocalApprovalStore(t)

	writeTestApprovals(t, testApprovals)
	runner := NewTestRunner(t)

	// Test list with pending filter
	_, err := runner.Execute("approval", "list", "--pending")
	if err != nil {
		t.Errorf("approval list --pending failed: %v", err)
	}
}

func TestApprovalShow(t *testing.T) {
	useLocalApprovalStore(t)
	runner := NewTestRunner(t)
	writeTestApprovals(t, testApprovals)

	_, err := runner.Execute("approval", "show", "01APP456XYZ")
	if err != nil {
		t.Errorf("approval show failed: %v", err)
	}
}

func TestApprovalShowNotFound(t *testing.T) {
	useLocalApprovalStore(t)

	writeTestApprovals(t, testApprovals)
	runner := NewTestRunner(t)

	// Test show with non-existent approval
	_, err := runner.Execute("approval", "show", "NONEXISTENT-999")
	if err == nil {
		t.Errorf("Expected error for non-existent approval, got nil")
	}
}

func TestApprovalDecideApprove(t *testing.T) {
	useLocalApprovalStore(t)
	runner := NewTestRunner(t)
	writeTestApprovals(t, testApprovals)

	_, err := runner.Execute("approval", "decide", "01APP456XYZ", "--approve", "--reason", "Looks good")
	if err != nil {
		t.Errorf("approval decide --approve failed: %v", err)
	}
}

func TestApprovalDecideReject(t *testing.T) {
	useLocalApprovalStore(t)
	runner := NewTestRunner(t)
	writeTestApprovals(t, testApprovals)

	_, err := runner.Execute("approval", "decide", "01APP789ABC", "--reject", "--reason", "Needs revision")
	if err != nil {
		t.Errorf("approval decide --reject failed: %v", err)
	}
}

func TestApprovalDecideMissingApproval(t *testing.T) {
	useLocalApprovalStore(t)
	runner := NewTestRunner(t)

	// Test decide without approval ID
	_, err := runner.Execute("approval", "decide")
	if err == nil {
		t.Errorf("Expected error for missing approval ID, got nil")
	}
}

func TestApprovalDecideMissingDecision(t *testing.T) {
	useLocalApprovalStore(t)

	writeTestApprovals(t, testApprovals)
	runner := NewTestRunner(t)

	// Reset flags in case a previous test set them (cobra uses global command instances).
	approvalDecideCmd.Flags().Set("approve", "false")
	approvalDecideCmd.Flags().Set("reject", "false")

	_, err := runner.Execute("approval", "decide", "01APP456XYZ")
	if err == nil {
		t.Errorf("Expected error for missing decision flag, got nil")
	}
}

func TestApprovalDecideJsonFormat(t *testing.T) {
	useLocalApprovalStore(t)
	runner := NewTestRunner(t)
	writeTestApprovals(t, testApprovals)

	_, err := runner.Execute("approval", "decide", "01APP456XYZ", "--approve", "--format", "json")
	if err != nil {
		t.Errorf("approval decide --format json failed: %v", err)
	}
}

func TestApprovalShowJsonFormat(t *testing.T) {
	useLocalApprovalStore(t)

	runner := NewTestRunner(t)
	writeTestApprovals(t, testApprovals)

	_, err := runner.Execute("approval", "show", "01APP456XYZ", "--format", "json")
	if err != nil {
		t.Errorf("approval show --format json failed: %v", err)
	}
}

func TestApprovalShowQuietFormat(t *testing.T) {
	useLocalApprovalStore(t)

	runner := NewTestRunner(t)
	writeTestApprovals(t, testApprovals)

	_, err := runner.Execute("approval", "show", "01APP456XYZ", "--format", "quiet")
	if err != nil {
		t.Errorf("approval show --format quiet failed: %v", err)
	}
}

func TestApprovalListEmpty(t *testing.T) {
	useLocalApprovalStore(t)

	// No approvals file — expect empty results (no error)
	runner := NewTestRunner(t)
	_, err := runner.Execute("approval", "list")
	if err != nil {
		t.Errorf("approval list with no data should succeed with empty results: %v", err)
	}
}

func TestApprovalLoadFromDirectory(t *testing.T) {
	useLocalApprovalStore(t)

	// Create empty approvals directory (no approvals.json)
	os.MkdirAll(".forge/approvals", 0755)

	runner := NewTestRunner(t)
	_, err := runner.Execute("approval", "list")
	if err != nil {
		t.Errorf("approval list with empty dir should succeed: %v", err)
	}
}

func TestApprovalSaveEmpty(t *testing.T) {
	useLocalApprovalStore(t)
	runner := NewTestRunner(t)
	writeTestApprovals(t, testApprovals)

	_, err := runner.Execute("approval", "decide", "01APP456XYZ", "--approve")
	if err != nil {
		t.Errorf("approval decide should work: %v", err)
	}
}
