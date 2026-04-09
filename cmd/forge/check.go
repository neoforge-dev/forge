// check.go — forge check: local validation before push
// Local validation checks before push.
//
// Three modes:
//
//	--quick   lint only (ruff check, eslint)                  <30s
//	default   standard (ruff check+format, eslint, tsc)       <2min
//	--full    standard + tests (go test, pytest fast, vitest) <10min
//
// --fix:   run ruff format + ruff check --fix
// --json:  output results as JSON
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// checkResult holds the outcome of a single validation check.
type checkResult struct {
	Name    string   `json:"name"`
	Passed  bool     `json:"passed"`
	Summary string   `json:"summary"`
	Details []string `json:"details"`
}

var checkCmd = &cobra.Command{
	Use:   "check",
	Short: "Local validation — runs lint, format, and type checks before push",
	Long: `forge check runs local validation checks to catch issues before pushing.

Three levels of checking:
  --quick    Lint only (ruff check, eslint)                   <30s
  (default)  Standard (ruff check + format, eslint, tsc)      <2min
  --full     Standard + tests (go test, pytest fast, vitest)  <10min

Auto-fix mode:
  --fix      Run ruff format + ruff check --fix

Detection logic (run from project root):
  go.mod present         → go build ./... (standard); go test ./... -short (full)
  pyproject.toml/setup.py → uv run ruff check/format .
  package.json           → npm run lint / npm run typecheck (if scripts exist)

Examples:
  forge check              # Standard checks
  forge check --quick      # Lint only
  forge check --full       # Lint + tests
  forge check --fix        # Auto-fix formatting/lint issues
  forge check --json       # Machine-readable output

Exit codes:
  0   All checks passed
  1   One or more checks failed`,
	RunE: runCheck,
}

func runCheck(cmd *cobra.Command, args []string) error {
	quick, _ := cmd.Flags().GetBool("quick")
	full, _ := cmd.Flags().GetBool("full")
	fix, _ := cmd.Flags().GetBool("fix")
	jsonOutput, _ := cmd.Flags().GetBool("json")

	cwd, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("could not determine working directory: %w", err)
	}

	if fix {
		return runCheckFix(cwd)
	}

	var results []checkResult
	start := time.Now()

	hasPython := checkFileExists(filepath.Join(cwd, "pyproject.toml")) ||
		checkFileExists(filepath.Join(cwd, "setup.py"))
	hasGo := checkFileExists(filepath.Join(cwd, "go.mod"))
	hasNode := checkFileExists(filepath.Join(cwd, "package.json"))

	// Also check harness/ subdirectory for Python if not in harness directly
	harnessDir := filepath.Join(cwd, "harness")
	hasPythonHarness := !hasPython && (checkFileExists(filepath.Join(harnessDir, "pyproject.toml")) ||
		checkFileExists(filepath.Join(harnessDir, "setup.py")))

	ccDir := filepath.Join(cwd, "harness", "command_center")
	hasCC := !hasNode && checkFileExists(filepath.Join(ccDir, "package.json"))

	// Determine Python target dir
	pythonDir := cwd
	if !hasPython && hasPythonHarness {
		pythonDir = harnessDir
		hasPython = true
	}

	// Determine JS target dir
	jsDir := cwd
	if !hasNode && hasCC {
		jsDir = ccDir
		hasNode = true
	}

	// ── ruff check (all levels, Python projects) ──
	if hasPython {
		r := execCheck("ruff check",
			[]string{"uv", "run", "ruff", "check", "."},
			pythonDir, 300)
		results = append(results, r)
	}

	// ── ruff format --check (standard + full, Python projects) ──
	if hasPython && !quick {
		r := execCheck("ruff format",
			[]string{"uv", "run", "ruff", "format", "--check", "."},
			pythonDir, 300)
		if r.Passed {
			r.Summary = "all clean"
		} else {
			// Count .py files needing formatting from detail lines
			var fileCount int
			for _, d := range r.Details {
				if strings.HasSuffix(d, ".py") || strings.Contains(d, "/") {
					fileCount++
				}
			}
			if fileCount > 0 {
				r.Summary = fmt.Sprintf("%d file(s) need formatting", fileCount)
			} else {
				r.Summary = "files need formatting"
			}
		}
		results = append(results, r)
	}

	// ── go build ./... (standard + full, Go projects) ──
	if hasGo && !quick {
		r := execCheck("go build",
			[]string{"go", "build", "./..."},
			cwd, 120)
		results = append(results, r)
	}

	// ── eslint (all levels, Node projects) ──
	if hasNode {
		lintScript := nodeScriptExists(jsDir, "lint")
		if lintScript {
			r := execCheck("eslint",
				[]string{"npm", "run", "lint"},
				jsDir, 120)
			if r.Passed {
				r.Summary = "0 warnings"
			}
			results = append(results, r)
		} else {
			// Fall back to direct npx eslint if script not defined
			r := execCheck("eslint",
				[]string{"npx", "eslint", "src/", "--max-warnings", "0"},
				jsDir, 120)
			if r.Passed {
				r.Summary = "0 warnings"
			}
			results = append(results, r)
		}
	}

	// ── tsc --noEmit (standard + full, Node projects with tsconfig) ──
	if hasNode && !quick && checkFileExists(filepath.Join(jsDir, "tsconfig.json")) {
		tcScript := nodeScriptExists(jsDir, "typecheck")
		if tcScript {
			r := execCheck("tsc --noEmit",
				[]string{"npm", "run", "typecheck"},
				jsDir, 180)
			results = append(results, r)
		} else {
			r := execCheck("tsc --noEmit",
				[]string{"npx", "tsc", "--noEmit"},
				jsDir, 180)
			results = append(results, r)
		}
	}

	// ── go test ./... -short (full only, Go projects) ──
	if hasGo && full {
		r := execCheck("go test (short)",
			[]string{"go", "test", "./...", "-short"},
			cwd, 600)
		results = append(results, r)
	}

	// ── pytest fast subset (full only, Python projects) ──
	if hasPython && full {
		r := execCheck("pytest (fast)",
			[]string{"uv", "run", "pytest", "tests/", "-x", "-q", "--timeout=60", "-m", "not slow"},
			pythonDir, 300)
		results = append(results, r)
	}

	// ── vitest (full only, Node projects) ──
	if hasNode && full {
		r := execCheck("vitest",
			[]string{"npx", "vitest", "run", "--reporter=verbose"},
			jsDir, 600)
		results = append(results, r)
	}

	elapsed := time.Since(start)

	if len(results) == 0 {
		fmt.Println("No checkable project files found (go.mod, pyproject.toml, package.json).")
		fmt.Println("Run from a project root or the FORGE repository root.")
		return nil
	}

	if jsonOutput {
		return printCheckJSON(results, elapsed)
	}
	printCheckDashboard(results, elapsed)

	for _, r := range results {
		if !r.Passed {
			return fmt.Errorf("one or more checks failed — run 'forge check --fix' to auto-fix")
		}
	}
	return nil
}

// execCheck runs a single check command and returns a checkResult.
// If the binary is not found it returns a skipped (passing) result.
func execCheck(name string, cmdArgs []string, dir string, timeoutSec int) checkResult {
	c := exec.Command(cmdArgs[0], cmdArgs[1:]...) //nolint:gosec
	c.Dir = dir

	var buf bytes.Buffer
	c.Stdout = &buf
	c.Stderr = &buf

	done := make(chan error, 1)
	go func() { done <- c.Run() }()

	timer := time.NewTimer(time.Duration(timeoutSec) * time.Second)
	defer timer.Stop()

	var runErr error
	select {
	case runErr = <-done:
		// completed
	case <-timer.C:
		if c.Process != nil {
			c.Process.Kill() //nolint:errcheck
		}
		return checkResult{
			Name:    name,
			Passed:  false,
			Summary: fmt.Sprintf("timed out (>%ds)", timeoutSec),
		}
	}

	if runErr != nil {
		// Check if binary not found
		if isNotFound(runErr) {
			return checkResult{
				Name:    name,
				Passed:  true,
				Summary: "skipped (not installed)",
			}
		}
		output := strings.TrimSpace(buf.String())
		lines := checkSplitLines(output, 15)
		lineCount := len(strings.Split(output, "\n"))
		return checkResult{
			Name:    name,
			Passed:  false,
			Summary: fmt.Sprintf("%d issue(s)", lineCount),
			Details: lines,
		}
	}

	return checkResult{
		Name:    name,
		Passed:  true,
		Summary: "0 errors",
	}
}

// runCheckFix runs ruff format + ruff check --fix in auto-fix mode.
func runCheckFix(cwd string) error {
	pythonDir := cwd
	if !checkFileExists(filepath.Join(cwd, "pyproject.toml")) && !checkFileExists(filepath.Join(cwd, "setup.py")) {
		harnessDir := filepath.Join(cwd, "harness")
		if checkFileExists(filepath.Join(harnessDir, "pyproject.toml")) {
			pythonDir = harnessDir
		}
	}

	fmt.Println("Auto-fixing...")

	// ruff format
	fmt.Print("  Running ruff format... ")
	out, err := checkRunCmd([]string{"uv", "run", "ruff", "format", "."}, pythonDir, 300)
	if err != nil {
		fmt.Printf("failed: %s\n", checkTruncate(out, 200))
	} else {
		fmt.Println("done")
	}

	// ruff check --fix
	fmt.Print("  Running ruff check --fix... ")
	out, err = checkRunCmd([]string{"uv", "run", "ruff", "check", "--fix", "."}, pythonDir, 300)
	if err != nil {
		fmt.Printf("some issues remain: %s\n", checkTruncate(out, 200))
		// Show remaining issues (up to 10 lines)
		for i, line := range strings.Split(out, "\n") {
			if i >= 10 {
				break
			}
			if strings.TrimSpace(line) != "" {
				fmt.Printf("    %s\n", line)
			}
		}
	} else {
		fmt.Println("done")
	}

	fmt.Println()
	fmt.Println("Fix complete. Run 'forge check' to verify.")
	return nil
}

// printCheckDashboard prints a concise human-readable summary.
func printCheckDashboard(results []checkResult, elapsed time.Duration) {
	total := len(results)
	passed := 0
	for _, r := range results {
		if r.Passed {
			passed++
		}
	}
	failed := total - passed

	fmt.Println("forge check")
	fmt.Println(strings.Repeat("═", 40))

	for _, r := range results {
		icon := colorGreen + "✓" + colorReset
		if !r.Passed {
			icon = colorRed + "✗" + colorReset
		}
		fmt.Printf(" %s %-20s %s\n", icon, r.Name, r.Summary)
		if !r.Passed {
			for i, d := range r.Details {
				if i >= 5 {
					break
				}
				fmt.Printf("   → %s\n", d)
			}
		}
	}

	fmt.Println(strings.Repeat("═", 40))
	if failed == 0 {
		fmt.Printf(" %sAll checks passed (%d/%d)%s  [%.1fs]\n",
			colorGreen, passed, total, colorReset, elapsed.Seconds())
	} else {
		fmt.Printf(" %s%d check(s) failed.%s Run 'forge check --fix' to auto-fix.  [%.1fs]\n",
			colorRed, failed, colorReset, elapsed.Seconds())
	}
}

// printCheckJSON emits machine-readable JSON output.
func printCheckJSON(results []checkResult, elapsed time.Duration) error {
	total := len(results)
	passed := 0
	for _, r := range results {
		if r.Passed {
			passed++
		}
	}
	out := map[string]interface{}{
		"checks":          results,
		"total":           total,
		"passed":          passed,
		"failed":          total - passed,
		"elapsed_seconds": math2Round(elapsed.Seconds()),
		"all_passed":      passed == total,
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(out); err != nil {
		return fmt.Errorf("json encode: %w", err)
	}
	return nil
}

// ── helpers ──────────────────────────────────────────────────────────────────

func checkFileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// nodeScriptExists returns true if package.json at dir contains the named script.
func nodeScriptExists(dir, script string) bool {
	data, err := os.ReadFile(filepath.Join(dir, "package.json"))
	if err != nil {
		return false
	}
	var pkg map[string]interface{}
	if err := json.Unmarshal(data, &pkg); err != nil {
		return false
	}
	scripts, ok := pkg["scripts"].(map[string]interface{})
	if !ok {
		return false
	}
	_, found := scripts[script]
	return found
}

func checkSplitLines(s string, max int) []string {
	var out []string
	for _, line := range strings.Split(s, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		out = append(out, line)
		if len(out) >= max {
			break
		}
	}
	return out
}

func checkTruncate(s string, n int) string {
	s = strings.TrimSpace(s)
	if len(s) > n {
		return s[:n] + "..."
	}
	return s
}

func isNotFound(err error) bool {
	if err == nil {
		return false
	}
	return strings.Contains(err.Error(), "executable file not found") ||
		strings.Contains(err.Error(), "no such file")
}

func checkRunCmd(cmdArgs []string, dir string, timeoutSec int) (string, error) {
	c := exec.Command(cmdArgs[0], cmdArgs[1:]...) //nolint:gosec
	c.Dir = dir
	var buf bytes.Buffer
	c.Stdout = &buf
	c.Stderr = &buf

	done := make(chan error, 1)
	go func() { done <- c.Run() }()

	timer := time.NewTimer(time.Duration(timeoutSec) * time.Second)
	defer timer.Stop()

	select {
	case err := <-done:
		return buf.String(), err
	case <-timer.C:
		if c.Process != nil {
			c.Process.Kill() //nolint:errcheck
		}
		return "", fmt.Errorf("timed out after %ds", timeoutSec)
	}
}

func math2Round(f float64) float64 {
	return float64(int(f*100+0.5)) / 100
}

func init() {
	checkCmd.Hidden = true
	checkCmd.Flags().BoolP("quick", "q", false, "Lint only (ruff check, eslint) — <30s")
	checkCmd.Flags().BoolP("full", "f", false, "Standard + tests (go test, pytest fast, vitest) — <10min")
	checkCmd.Flags().Bool("fix", false, "Auto-fix formatting and lint issues (ruff format + ruff check --fix)")
	checkCmd.Flags().Bool("json", false, "Output results as JSON")
	checkCmd.Flags().String("task", "", "Task ID for runtime compatibility (currently informational)")
	checkCmd.Flags().Bool("post", false, "Run post-implementation checks (currently informational)")

	rootCmd.AddCommand(checkCmd)
}
