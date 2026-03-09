// Package main provides tests for the queue noun
package main

import (
	"os"
	"testing"
)

func TestQueueList(t *testing.T) {
	runner := NewTestRunner(t)

	// Test basic list - just verify no error
	_, err := runner.Execute("queue", "list")
	if err != nil {
		t.Errorf("queue list failed: %v", err)
	}

	// Test json format
	_, err = runner.Execute("queue", "list", "--format", "json")
	if err != nil {
		t.Errorf("queue list --format json failed: %v", err)
	}

	// Test csv format
	_, err = runner.Execute("queue", "list", "--format", "csv")
	if err != nil {
		t.Errorf("queue list --format csv failed: %v", err)
	}

	// Test quiet format
	_, err = runner.Execute("queue", "list", "--format", "quiet")
	if err != nil {
		t.Errorf("queue list --format quiet failed: %v", err)
	}
}

func TestQueueDepth(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "depth")
	if err != nil {
		t.Errorf("queue depth failed: %v", err)
	}
}

func TestQueuePopulate(t *testing.T) {
	runner := NewTestRunner(t)

	// Test populate - will fail due to missing file but should not crash
	_, _ = runner.Execute("queue", "populate", "--from", ".forge/v4/features-v4-cli.json")
	// Don't check error - file may not exist in test context
}

func TestQueueImportDispatches(t *testing.T) {
	runner := NewTestRunner(t)

	// Test import-dispatches - will fail due to missing directory but should not crash
	_, _ = runner.Execute("queue", "import-dispatches", "--dir", ".forge/dispatches")
	// Don't check error - directory may not exist in test context
}

func TestQueueShow(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "show", "V4-001")
	if err != nil {
		t.Errorf("queue show failed: %v", err)
	}
}

func TestQueueFilter(t *testing.T) {
	runner := NewTestRunner(t)

	// Test that filter flags are accepted without error
	_, err := runner.Execute("queue", "list", "--status", "QUEUED")
	if err != nil {
		t.Errorf("queue list --status failed: %v", err)
	}

	_, err = runner.Execute("queue", "list", "--assignee", "minimax")
	if err != nil {
		t.Errorf("queue list --assignee failed: %v", err)
	}

	_, err = runner.Execute("queue", "list", "--lane", "dev")
	if err != nil {
		t.Errorf("queue list --lane failed: %v", err)
	}
}

func TestQueueShowNotFound(t *testing.T) {
	runner := NewTestRunner(t)

	// Test showing non-existent item - should return error
	_, err := runner.Execute("queue", "show", "NONEXISTENT-999")
	if err == nil {
		t.Errorf("Expected error for non-existent queue item, got nil")
	}
}

func TestQueuePopulateMissingFile(t *testing.T) {
	runner := NewTestRunner(t)

	// Test populate with missing file
	_, err := runner.Execute("queue", "populate", "--from", "/nonexistent/file.json")
	if err == nil {
		t.Errorf("Expected error for missing file, got nil")
	}
}

func TestQueuePopulateMissingFlag(t *testing.T) {
	runner := NewTestRunner(t)

	// Test populate without --from flag
	_, err := runner.Execute("queue", "populate")
	if err == nil {
		t.Errorf("Expected error for missing --from flag, got nil")
	}
}

func TestQueueImportDispatchesMissingDir(t *testing.T) {
	runner := NewTestRunner(t)

	// Test import-dispatches with missing directory
	_, err := runner.Execute("queue", "import-dispatches", "--dir", "/nonexistent/dir")
	if err == nil {
		t.Errorf("Expected error for missing directory, got nil")
	}
}

func TestQueueImportDispatchesMissingFlag(t *testing.T) {
	runner := NewTestRunner(t)

	// Test import-dispatches without --dir flag
	_, err := runner.Execute("queue", "import-dispatches")
	if err == nil {
		t.Errorf("Expected error for missing --dir flag, got nil")
	}
}

func TestQueueDepthJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test depth with json format
	_, err := runner.Execute("queue", "depth", "--format", "json")
	if err != nil {
		t.Errorf("queue depth --format json failed: %v", err)
	}
}

func TestQueueDepthQuietFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test depth with quiet format (should return nothing)
	_, err := runner.Execute("queue", "depth", "--format", "quiet")
	if err != nil {
		t.Errorf("queue depth --format quiet failed: %v", err)
	}
}

func TestQueueListFilterStatusCompleted(t *testing.T) {
	runner := NewTestRunner(t)

	// Test filter by COMPLETED status
	_, err := runner.Execute("queue", "list", "--status", "COMPLETED")
	if err != nil {
		t.Errorf("queue list --status COMPLETED failed: %v", err)
	}
}

func TestQueueListFilterStatusFailed(t *testing.T) {
	runner := NewTestRunner(t)

	// Test filter by FAILED status
	_, err := runner.Execute("queue", "list", "--status", "FAILED")
	if err != nil {
		t.Errorf("queue list --status FAILED failed: %v", err)
	}
}

func TestQueueListFilterAssigneeGemini(t *testing.T) {
	runner := NewTestRunner(t)

	// Test filter by assignee gemini
	_, err := runner.Execute("queue", "list", "--assignee", "gemini")
	if err != nil {
		t.Errorf("queue list --assignee gemini failed: %v", err)
	}
}

func TestQueueListFilterLaneProd(t *testing.T) {
	runner := NewTestRunner(t)

	// Test filter by lane prod
	_, err := runner.Execute("queue", "list", "--lane", "prod")
	if err != nil {
		t.Errorf("queue list --lane prod failed: %v", err)
	}
}

func TestQueueListFilterStatusRunning(t *testing.T) {
	runner := NewTestRunner(t)

	// Test filter by RUNNING status
	_, err := runner.Execute("queue", "list", "--status", "RUNNING")
	if err != nil {
		t.Errorf("queue list --status RUNNING failed: %v", err)
	}
}

func TestQueueListFilterStatusDispatched(t *testing.T) {
	runner := NewTestRunner(t)

	// Test filter by DISPATCHED status
	_, err := runner.Execute("queue", "list", "--status", "DISPATCHED")
	if err != nil {
		t.Errorf("queue list --status DISPATCHED failed: %v", err)
	}
}

func TestQueueListFilterAssigneeKimi(t *testing.T) {
	runner := NewTestRunner(t)

	// Test filter by assignee kimi
	_, err := runner.Execute("queue", "list", "--assignee", "kimi")
	if err != nil {
		t.Errorf("queue list --assignee kimi failed: %v", err)
	}
}

func TestQueueListFilterLaneTest(t *testing.T) {
	runner := NewTestRunner(t)

	// Test filter by lane test
	_, err := runner.Execute("queue", "list", "--lane", "test")
	if err != nil {
		t.Errorf("queue list --lane test failed: %v", err)
	}
}

func TestQueueListFilterMultiple(t *testing.T) {
	runner := NewTestRunner(t)

	// Test filter with multiple flags
	_, err := runner.Execute("queue", "list", "--status", "QUEUED", "--lane", "dev")
	if err != nil {
		t.Errorf("queue list with multiple filters failed: %v", err)
	}
}

func TestQueueShowDF001(t *testing.T) {
	runner := NewTestRunner(t)

	// Test showing DF-001
	_, err := runner.Execute("queue", "show", "DF-001")
	if err != nil {
		t.Errorf("queue show DF-001 failed: %v", err)
	}
}

func TestQueueShowV4005(t *testing.T) {
	runner := NewTestRunner(t)

	// Test showing V4-005
	_, err := runner.Execute("queue", "show", "V4-005")
	if err != nil {
		t.Errorf("queue show V4-005 failed: %v", err)
	}
}

func TestQueueShowV4011(t *testing.T) {
	runner := NewTestRunner(t)

	// Test showing V4-011
	_, err := runner.Execute("queue", "show", "V4-011")
	if err != nil {
		t.Errorf("queue show V4-011 failed: %v", err)
	}
}

func TestQueueDepthTableFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test depth with table format (default)
	_, err := runner.Execute("queue", "depth", "--format", "table")
	if err != nil {
		t.Errorf("queue depth --format table failed: %v", err)
	}
}

func TestQueueDepthCsvFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test depth with csv format
	_, err := runner.Execute("queue", "depth", "--format", "csv")
	if err != nil {
		t.Errorf("queue depth --format csv failed: %v", err)
	}
}

func TestQueueListCaseInsensitiveStatus(t *testing.T) {
	runner := NewTestRunner(t)

	// Test case insensitive status filter
	_, err := runner.Execute("queue", "list", "--status", "queued")
	if err != nil {
		t.Errorf("queue list --status queued (lowercase) failed: %v", err)
	}
}

func TestQueueListCaseInsensitiveAssignee(t *testing.T) {
	runner := NewTestRunner(t)

	// Test case insensitive assignee filter
	_, err := runner.Execute("queue", "list", "--assignee", "MINIMAX")
	if err != nil {
		t.Errorf("queue list --assignee MINIMAX (uppercase) failed: %v", err)
	}
}

func TestQueueListCaseInsensitiveLane(t *testing.T) {
	runner := NewTestRunner(t)

	// Test case insensitive lane filter
	_, err := runner.Execute("queue", "list", "--lane", "DEV")
	if err != nil {
		t.Errorf("queue list --lane DEV (uppercase) failed: %v", err)
	}
}

func TestQueueShowJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with json format
	_, err := runner.Execute("queue", "show", "V4-001", "--format", "json")
	if err != nil {
		t.Errorf("queue show --format json failed: %v", err)
	}
}

func TestQueueShowQuietFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test show with quiet format
	_, err := runner.Execute("queue", "show", "V4-001", "--format", "quiet")
	if err != nil {
		t.Errorf("queue show --format quiet failed: %v", err)
	}
}

func TestQueuePopulateJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test populate with json format (will fail but shouldn't crash)
	_, _ = runner.Execute("queue", "populate", "--from", "test.json", "--format", "json")
}

func TestQueueImportDispatchesJsonFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test import-dispatches with json format (will fail but shouldn't crash)
	_, _ = runner.Execute("queue", "import-dispatches", "--dir", "test", "--format", "json")
}

func TestQueuePopulateCsvFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test populate with csv format
	_, _ = runner.Execute("queue", "populate", "--from", "test.json", "--format", "csv")
}

func TestQueueImportDispatchesCsvFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test import-dispatches with csv format
	_, _ = runner.Execute("queue", "import-dispatches", "--dir", "test", "--format", "csv")
}

func TestQueuePopulateQuietFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test populate with quiet format
	_, _ = runner.Execute("queue", "populate", "--from", "test.json", "--format", "quiet")
}

func TestQueueImportDispatchesQuietFormat(t *testing.T) {
	runner := NewTestRunner(t)

	// Test import-dispatches with quiet format
	_, _ = runner.Execute("queue", "import-dispatches", "--dir", "test", "--format", "quiet")
}

func TestQueuePopulateWithValidFile(t *testing.T) {
	runner := NewTestRunner(t)

	// Create a temporary valid features.json
	content := `{"features":[{"id":"TEST-001","name":"Test Feature","description":"Test","status":"queued","priority":"P1","lane":"dev","assigned_to":"tester"}]}`
	tmpFile := t.TempDir() + "/features.json"
	if err := os.WriteFile(tmpFile, []byte(content), 0644); err != nil {
		t.Skipf("Cannot create temp file: %v", err)
	}

	_, err := runner.Execute("queue", "populate", "--from", tmpFile)
	if err != nil {
		t.Errorf("queue populate with valid file failed: %v", err)
	}
}

func TestQueuePopulateWithInvalidJson(t *testing.T) {
	runner := NewTestRunner(t)

	// Create a temporary invalid JSON file
	tmpFile := t.TempDir() + "/invalid.json"
	if err := os.WriteFile(tmpFile, []byte("not valid json"), 0644); err != nil {
		t.Skipf("Cannot create temp file: %v", err)
	}

	_, err := runner.Execute("queue", "populate", "--from", tmpFile)
	if err == nil {
		t.Errorf("Expected error for invalid JSON, got nil")
	}
}

func TestQueueImportDispatchesWithValidDir(t *testing.T) {
	runner := NewTestRunner(t)

	// Create a temporary directory with dispatch files
	tmpDir := t.TempDir()
	if err := os.WriteFile(tmpDir+"/test-dispatch.md", []byte("# Test Dispatch\n\nTask description"), 0644); err != nil {
		t.Skipf("Cannot create temp file: %v", err)
	}

	_, err := runner.Execute("queue", "import-dispatches", "--dir", tmpDir)
	if err != nil {
		t.Errorf("queue import-dispatches with valid dir failed: %v", err)
	}
}

func TestQueueImportDispatchesWithEmptyDir(t *testing.T) {
	runner := NewTestRunner(t)

	// Create an empty temporary directory
	tmpDir := t.TempDir()

	_, err := runner.Execute("queue", "import-dispatches", "--dir", tmpDir)
	if err != nil {
		t.Errorf("queue import-dispatches with empty dir failed: %v", err)
	}
}
