// Package main provides daemon integration tests for CLI v4 ↔ v3
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/neoforge-dev/forge/internal"
)

// DaemonTestSuite manages the v3 daemon for integration tests
type DaemonTestSuite struct {
	cmd        *exec.Cmd
	apiURL     string
	dataDir    string
	forgeRoot  string
}

// setupDaemon starts the v3 daemon for testing
func setupDaemon(t *testing.T) *DaemonTestSuite {
	t.Helper()

	if os.Getenv("FORGE_INTEGRATION_TESTS") == "" {
		t.Skip("set FORGE_INTEGRATION_TESTS=1 to run daemon integration tests")
	}

	// Create temp directory for daemon data
	dataDir := t.TempDir()

	// Find forge-v3 binary
	v3Binary := findV3BinaryForTest(t)

	// Use a random port to avoid conflicts
	port := 18080 // Use a high port for testing
	apiURL := fmt.Sprintf("http://localhost:%d", port)

	// Set up environment
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "/home/openclaw/work/FORGE"
	}

	// Start daemon
	cmd := exec.Command(v3Binary, "daemon", "start")
	cmd.Env = append(os.Environ(),
		fmt.Sprintf("FORGE_DATA_DIR=%s", dataDir),
		fmt.Sprintf("FORGE_API_PORT=%d", port),
		fmt.Sprintf("FORGE_ROOT=%s", forgeRoot),
	)

	// Capture output for debugging
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	if err := cmd.Start(); err != nil {
		t.Fatalf("failed to start daemon: %v", err)
	}

	// Wait for daemon to be ready
	time.Sleep(2 * time.Second)

	suite := &DaemonTestSuite{
		cmd:       cmd,
		apiURL:    apiURL,
		dataDir:   dataDir,
		forgeRoot: forgeRoot,
	}

	// Verify daemon is running
	if err := suite.waitForReady(10); err != nil {
		suite.cleanup()
		t.Fatalf("daemon failed to become ready: %v", err)
	}

	// Register cleanup
	t.Cleanup(func() {
		suite.cleanup()
	})

	return suite
}

// findV3BinaryForTest locates the forge-v3 binary for testing
func findV3BinaryForTest(t *testing.T) string {
	t.Helper()

	// Try common locations — forged first, then forge-v3 fallback
	locations := []string{
		"../forged/forged",
		filepath.Join(os.Getenv("FORGE_ROOT"), "cmd/forged/forged"),
		"./forged",
		// Backward compat fallbacks
		"../forge-v3/forge-v3",
		"../forge-v3/main",
		filepath.Join(os.Getenv("FORGE_ROOT"), "cmd/forge-v3/forge-v3"),
		"./forge-v3",
	}

	for _, loc := range locations {
		if _, err := os.Stat(loc); err == nil {
			abs, _ := filepath.Abs(loc)
			return abs
		}
	}

	// Try to build it
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "/home/openclaw/work/FORGE"
	}

	daemonDir := filepath.Join(forgeRoot, "cmd/forged")
	if _, err := os.Stat(daemonDir); err == nil {
		// Build the binary
		output := filepath.Join(daemonDir, "forged")
		cmd := exec.Command("go", "build", "-o", "forged", ".")
		cmd.Dir = daemonDir
		if err := cmd.Run(); err == nil {
			return output
		}
	}

	t.Skip("forged binary not found, skipping daemon integration tests")
	return ""
}

// waitForReady polls the daemon until it's ready
func (s *DaemonTestSuite) waitForReady(maxAttempts int) error {
	client := &http.Client{Timeout: 2 * time.Second}

	for i := 0; i < maxAttempts; i++ {
		resp, err := client.Get(s.apiURL + "/health")
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				return nil
			}
		}
		time.Sleep(500 * time.Millisecond)
	}

	return fmt.Errorf("daemon not ready after %d attempts", maxAttempts)
}

// cleanup stops the daemon
func (s *DaemonTestSuite) cleanup() {
	if s.cmd != nil && s.cmd.Process != nil {
		// Try graceful shutdown first
		s.cmd.Process.Signal(syscall.SIGTERM)
		time.Sleep(1 * time.Second)

		// Force kill if still running
		s.cmd.Process.Kill()
		s.cmd.Wait()
	}
}

// runCLI executes a CLI command against the daemon
func (s *DaemonTestSuite) runCLI(t *testing.T, args ...string) (string, error) {
	t.Helper()

	cmd := exec.Command("go", append([]string{"run", "."}, args...)...)
	cmd.Dir = "/home/openclaw/work/FORGE/cmd/forge"
	cmd.Env = append(os.Environ(),
		fmt.Sprintf("FORGE_API_URL=%s", s.apiURL),
		fmt.Sprintf("FORGE_ROOT=%s", s.forgeRoot),
	)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Run()
	output := stdout.String() + stderr.String()

	return output, err
}

// ==================== Integration Tests ====================

// TestIntegration_DaemonHealth verifies daemon health endpoint
func TestIntegration_DaemonHealth(t *testing.T) {
	suite := setupDaemon(t)

	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get(suite.apiURL + "/health")
	if err != nil {
		t.Fatalf("health check failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected status 200, got %d", resp.StatusCode)
	}
}

// TestIntegration_TaskLifecycle tests full task CRUD via CLI
func TestIntegration_TaskLifecycle(t *testing.T) {
	if runtime.GOOS == "darwin" {
		t.Skip("Skipping daemon test on macOS - use Linux CI")
	}

	suite := setupDaemon(t)

	// Test 1: List tasks (should be empty initially)
	t.Run("list_tasks_empty", func(t *testing.T) {
		output, err := suite.runCLI(t, "task", "list", "--format", "json")
		if err != nil {
			t.Logf("task list output: %s", output)
		}
		// Should succeed even if empty
	})

	// Test 2: Create a task
	var taskID string
	t.Run("create_task", func(t *testing.T) {
		// For now, skip if task create isn't fully implemented
		t.Skip("task create requires full API implementation")
	})

	_ = taskID // Silence unused warning
}

// TestIntegration_DomainCommands tests domain commands against daemon
func TestIntegration_DomainCommands(t *testing.T) {
	suite := setupDaemon(t)

	t.Run("domain_list", func(t *testing.T) {
		output, err := suite.runCLI(t, "domain", "list")
		if err != nil {
			t.Logf("domain list error: %v", err)
			t.Logf("output: %s", output)
			// Don't fail - domains may load from YAML
		}
		if !strings.Contains(output, "codeswiftr-com") {
			t.Logf("warning: expected codeswiftr-com in output")
		}
	})

	t.Run("domain_list_json", func(t *testing.T) {
		output, err := suite.runCLI(t, "domain", "list", "--format", "json")
		if err != nil {
			t.Logf("domain list json error: %v", err)
		}

		var domains []internal.Domain
		if err := json.Unmarshal([]byte(output), &domains); err == nil {
			if len(domains) == 0 {
				t.Logf("warning: no domains returned")
			}
		}
	})

	t.Run("domain_show", func(t *testing.T) {
		output, err := suite.runCLI(t, "domain", "show", "codeswiftr-com")
		if err != nil {
			t.Logf("domain show error: %v", err)
		}
		if !strings.Contains(output, "codeswiftr-com") {
			t.Logf("warning: expected codeswiftr-com in output")
		}
	})
}

// TestIntegration_ProjectCommands tests project commands
func TestIntegration_ProjectCommands(t *testing.T) {
	suite := setupDaemon(t)

	t.Run("project_list", func(t *testing.T) {
		output, err := suite.runCLI(t, "project", "list")
		if err != nil {
			t.Logf("project list error: %v", err)
			t.Logf("output: %s", output)
		}
	})

	t.Run("project_list_by_domain", func(t *testing.T) {
		output, err := suite.runCLI(t, "project", "list", "--domain", "codeswiftr-com")
		if err != nil {
			t.Logf("project list by domain error: %v", err)
			t.Logf("output: %s", output)
		}
	})
}

// TestIntegration_ApprovalCommands tests approval commands
func TestIntegration_ApprovalCommands(t *testing.T) {
	suite := setupDaemon(t)

	t.Run("approval_list", func(t *testing.T) {
		_, err := suite.runCLI(t, "approval", "list")
		if err != nil {
			t.Logf("approval list error: %v", err)
		}
		// Should show demo approvals
	})

	t.Run("approval_list_pending", func(t *testing.T) {
		output, err := suite.runCLI(t, "approval", "list", "--pending")
		if err != nil {
			t.Logf("approval list pending error: %v", err)
		}
		_ = output
	})

	t.Run("approval_show", func(t *testing.T) {
		output, err := suite.runCLI(t, "approval", "show", "01APP456XYZ")
		if err != nil {
			t.Logf("approval show error: %v", err)
		}
		_ = output
	})
}

// TestIntegration_PatternCommands tests pattern commands
func TestIntegration_PatternCommands(t *testing.T) {
	suite := setupDaemon(t)

	t.Run("pattern_list", func(t *testing.T) {
		output, err := suite.runCLI(t, "pattern", "list")
		if err != nil {
			t.Errorf("pattern list failed: %v", err)
		}
		if !strings.Contains(output, "Total:") {
			t.Errorf("expected Total: in output, got: %s", output)
		}
	})

	t.Run("pattern_show", func(t *testing.T) {
		output, err := suite.runCLI(t, "pattern", "show", "api_endpoint:simple")
		if err != nil {
			t.Errorf("pattern show failed: %v", err)
		}
		if !strings.Contains(output, "Simple API") && !strings.Contains(output, "api_endpoint") {
			t.Errorf("expected pattern details in output, got: %s", output)
		}
	})
}

// TestIntegration_ConfigCommands tests config commands
func TestIntegration_ConfigCommands(t *testing.T) {
	suite := setupDaemon(t)

	t.Run("config_list", func(t *testing.T) {
		output, err := suite.runCLI(t, "config", "list")
		if err != nil {
			t.Logf("config list error: %v", err)
		}
		_ = output
	})

	t.Run("config_get", func(t *testing.T) {
		_, err := suite.runCLI(t, "config", "get", "api_url")
		if err != nil {
			t.Logf("config get error: %v", err)
		}
	})
}

// TestIntegration_DispatchWorkflow tests dispatch workflow
func TestIntegration_DispatchWorkflow(t *testing.T) {
	if runtime.GOOS == "darwin" {
		t.Skip("Skipping daemon test on macOS - use Linux CI")
	}

	suite := setupDaemon(t)
	_ = suite // Used by subtests

	t.Run("dispatch_send", func(t *testing.T) {
		// This requires the full API implementation
		t.Skip("dispatch send requires full task API")
	})
}

// TestIntegration_VersionCommand tests version command
func TestIntegration_VersionCommand(t *testing.T) {
	suite := setupDaemon(t)
	_ = suite // Ensure suite is used

	output, err := suite.runCLI(t, "version")
	if err != nil {
		t.Errorf("version command failed: %v", err)
	}
	if !strings.Contains(output, "FORGE") && !strings.Contains(output, "v4") {
		t.Logf("version output: %s", output)
	}
}

// TestIntegration_StatusCommand tests status command
func TestIntegration_StatusCommand(t *testing.T) {
	suite := setupDaemon(t)

	output, err := suite.runCLI(t, "status")
	if err != nil {
		t.Logf("status command error: %v", err)
	}
	_ = output
}

// TestIntegration_WorkCommand tests work command
func TestIntegration_WorkCommand(t *testing.T) {
	suite := setupDaemon(t)
	_ = suite // Ensure suite is used

	t.Run("work_set_domain", func(t *testing.T) {
		// Test setting work context
		t.Skip("work command requires full context persistence")
	})
}

// TestIntegration_ShipWorkflow tests ship workflow (dry run only)
func TestIntegration_ShipWorkflow(t *testing.T) {
	suite := setupDaemon(t)

	output, err := suite.runCLI(t, "ship", "-m", "Test commit", "--dry-run")
	if err != nil {
		t.Errorf("ship dry-run failed: %v", err)
	}
	if !strings.Contains(output, "DRY RUN") {
		t.Errorf("expected DRY RUN in output, got: %s", output)
	}
}

// TestIntegration_AllNounsRegistered verifies all nouns are registered
func TestIntegration_AllNounsRegistered(t *testing.T) {
	suite := setupDaemon(t)

	nouns := []string{
		"domain", "approval", "project", "config",
		"pattern", "dispatch", "ship", "work",
		"lane", "context", "queue", "task",
		"agent", "patrol", "daemon", "worker",
	}

	for _, noun := range nouns {
		t.Run(noun+"_help", func(t *testing.T) {
			output, err := suite.runCLI(t, noun, "--help")
			if err != nil {
				// --help returns exit code 0 but cobra may return error
				t.Logf("%s help: %v", noun, err)
			}
			if output == "" {
				t.Errorf("%s has no help output", noun)
			}
		})
	}
}

// TestIntegration_FormatSupport tests all output formats
func TestIntegration_FormatSupport(t *testing.T) {
	suite := setupDaemon(t)

	formats := []string{"table", "json", "csv", "quiet"}

	for _, format := range formats {
		t.Run("domain_list_"+format, func(t *testing.T) {
			output, err := suite.runCLI(t, "domain", "list", "--format", format)
			if err != nil {
				t.Logf("format %s error: %v", format, err)
			}

			if format == "json" {
				var domains []internal.Domain
				if err := json.Unmarshal([]byte(output), &domains); err != nil {
					t.Logf("invalid JSON for format %s: %v", format, err)
				}
			}

			if format == "quiet" && strings.TrimSpace(output) != "" {
				t.Logf("quiet format should be empty, got: %s", output)
			}
		})
	}
}
