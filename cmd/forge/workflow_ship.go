package main

import (
	"fmt"
	"os"
	"os/exec"
	"strings"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// shipCmd represents the ship workflow command
// Usage: forge ship -m "commit message"
var shipCmd = &cobra.Command{
	Use:   "ship",
	Short: "Complete and ship current work",
	Long: `Ship current changes by running quality gates, committing, and pushing.

This workflow command performs the following steps:
  1. Runs quality gates (lint, type-check, tests if configured)
  2. Commits changes with the provided message
  3. Pushes to origin
  4. Triggers approval workflow if required

Examples:
  # Ship with a feature commit message
  forge ship -m "feat(auth): add OAuth2 login"

  # Ship with detailed message
  forge ship -m "fix: resolve race condition in task queue" --verbose

  # Dry run - show what would happen without executing
  forge ship -m "feat: new feature" --dry-run`,
}

// shipRunCmd: forge ship -m "message"
var shipRunCmd = &cobra.Command{
	Use:   "run",
	Short: "Run the ship workflow",
	Long:  "Execute quality gates, commit, and push changes.",
	RunE: func(cmd *cobra.Command, args []string) error {
		message, _ := cmd.Flags().GetString("message")
		format, _ := cmd.Flags().GetString("format")
		dryRun, _ := cmd.Flags().GetBool("dry-run")
		skipChecks, _ := cmd.Flags().GetBool("skip-checks")
		approve, _ := cmd.Flags().GetBool("approve")

		if message == "" {
			return fmt.Errorf("commit message is required (use -m 'message')")
		}

		formatter := internal.NewFormatter(format, nil)

		if dryRun {
			formatter.Println("[DRY RUN] Would execute the following:")
			formatter.Printf("  1. Run quality gates (unless --skip-checks)\n")
			formatter.Printf("  2. git add -A\n")
			formatter.Printf("  3. git commit -m \"%s\"\n", message)
			formatter.Printf("  4. git push origin main\n")
			if approve {
				formatter.Printf("  5. Auto-approve if confidence >= threshold\n")
			}
			return nil
		}

		// Step 1: Quality Gates (unless skipped)
		if !skipChecks {
			formatter.Println("Running quality gates...")

			// Check if there are changes to commit
			hasChanges, err := hasUncommittedChanges()
			if err != nil {
				return fmt.Errorf("failed to check git status: %w", err)
			}
			if !hasChanges {
				formatter.Println("No changes to ship")
				return nil
			}

			// Run lint if available
			if err := runQualityGate("lint", formatter); err != nil {
				return fmt.Errorf("lint check failed: %w\nUse --skip-checks to bypass", err)
			}

			// Run type check if available
			if err := runQualityGate("type-check", formatter); err != nil {
				return fmt.Errorf("type check failed: %w\nUse --skip-checks to bypass", err)
			}

			// Run tests if available
			if err := runQualityGate("test", formatter); err != nil {
				return fmt.Errorf("tests failed: %w\nUse --skip-checks to bypass", err)
			}
		}

		// Step 2: Stage all changes
		formatter.Println("Staging changes...")
		if err := gitAddAll(); err != nil {
			return fmt.Errorf("failed to stage changes: %w", err)
		}

		// Step 3: Commit
		formatter.Printf("Committing: %s\n", message)
		if err := gitCommit(message); err != nil {
			return fmt.Errorf("failed to commit: %w", err)
		}

		// Step 4: Push
		formatter.Println("Pushing to origin...")
		branch := getCurrentBranch()
		if err := gitPush(branch); err != nil {
			return fmt.Errorf("failed to push: %w", err)
		}

		// Step 5: Approval workflow (if requested)
		if approve {
			formatter.Println("Checking approval requirements...")
			// In a real implementation, this would check confidence scores
			// and trigger approval workflow if needed
			formatter.Println("Auto-approval requested (implementation pending)")
		}

		// Success output
		formatter.Println("")
		formatter.Println("✓ Shipped successfully!")
		formatter.Printf("  Commit: %s\n", getLastCommitHash())
		formatter.Printf("  Branch: %s\n", branch)

		return nil
	},
}

// Helper functions

func hasUncommittedChanges() (bool, error) {
	cmd := exec.Command("git", "status", "--porcelain")
	output, err := cmd.Output()
	if err != nil {
		return false, err
	}
	return len(strings.TrimSpace(string(output))) > 0, nil
}

func runQualityGate(gate string, formatter *internal.Formatter) error {
	// Check if a makefile or package.json has the target/script
	var cmd *exec.Cmd

	switch gate {
	case "lint":
		// Try common lint commands
		if fileExists("Makefile") {
			cmd = exec.Command("make", "lint")
		} else if fileExists("package.json") {
			cmd = exec.Command("npm", "run", "lint")
		} else if fileExists("pyproject.toml") || fileExists("setup.py") {
			cmd = exec.Command("python", "-m", "flake8", ".")
		} else {
			// No lint configured, skip
			return nil
		}
	case "type-check":
		if fileExists("Makefile") {
			cmd = exec.Command("make", "type-check")
		} else if fileExists("package.json") {
			// Check if typecheck script exists
			if hasNpmScript("typecheck") {
				cmd = exec.Command("npm", "run", "typecheck")
			} else {
				return nil
			}
		} else {
			return nil
		}
	case "test":
		if fileExists("Makefile") {
			cmd = exec.Command("make", "test")
		} else if fileExists("package.json") {
			if hasNpmScript("test") {
				cmd = exec.Command("npm", "test")
			} else {
				return nil
			}
		} else if fileExists("go.mod") {
			cmd = exec.Command("go", "test", "./...")
		} else if fileExists("pyproject.toml") || fileExists("setup.py") {
			cmd = exec.Command("python", "-m", "pytest", "-xvs")
		} else {
			return nil
		}
	}

	if cmd == nil {
		return nil
	}

	formatter.Printf("  Running %s...\n", gate)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s: %s", gate, string(output))
	}
	formatter.Printf("  ✓ %s passed\n", gate)
	return nil
}

func gitAddAll() error {
	cmd := exec.Command("git", "add", "-A")
	return cmd.Run()
}

func gitCommit(message string) error {
	cmd := exec.Command("git", "commit", "-m", message)
	output, err := cmd.CombinedOutput()
	if err != nil {
		// Check if it's a "nothing to commit" error
		if strings.Contains(string(output), "nothing to commit") {
			return fmt.Errorf("nothing to commit")
		}
		return fmt.Errorf("%s", string(output))
	}
	return nil
}

func gitPush(branch string) error {
	cmd := exec.Command("git", "push", "origin", branch)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s", string(output))
	}
	return nil
}

func getCurrentBranch() string {
	cmd := exec.Command("git", "rev-parse", "--abbrev-ref", "HEAD")
	output, err := cmd.Output()
	if err != nil {
		return "main"
	}
	return strings.TrimSpace(string(output))
}

func getLastCommitHash() string {
	cmd := exec.Command("git", "rev-parse", "--short", "HEAD")
	output, err := cmd.Output()
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(output))
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func hasNpmScript(script string) bool {
	// Quick check if package.json contains the script
	data, err := os.ReadFile("package.json")
	if err != nil {
		return false
	}
	return strings.Contains(string(data), `"`+script+`"`)
}

func init() {
	shipCmd.Hidden = true
	// Add flags
	shipRunCmd.Flags().StringP("message", "m", "", "Commit message (required)")
	shipRunCmd.Flags().Bool("dry-run", false, "Show what would happen without executing")
	shipRunCmd.Flags().Bool("skip-checks", false, "Skip quality gates")
	shipRunCmd.Flags().Bool("approve", false, "Auto-approve if confidence >= threshold")

	// Allow shorthand: forge ship -m "message"
	// This makes the "run" subcommand implicit
	shipCmd.RunE = func(cmd *cobra.Command, args []string) error {
		// Parse all flags and pass to shipRunCmd
		message, _ := cmd.Flags().GetString("message")
		if message == "" && len(args) > 0 {
			// Maybe they did: forge ship "message"
			message = args[0]
		}
		if message == "" {
			return fmt.Errorf("commit message is required (use -m 'message' or forge ship run -m 'message')")
		}
		
		// Copy all relevant flags
		dryRun, _ := cmd.Flags().GetBool("dry-run")
		skipChecks, _ := cmd.Flags().GetBool("skip-checks")
		approve, _ := cmd.Flags().GetBool("approve")
		format, _ := cmd.Flags().GetString("format")
		
		// Set flags on shipRunCmd
		shipRunCmd.Flags().Set("message", message)
		shipRunCmd.Flags().Set("dry-run", fmt.Sprintf("%v", dryRun))
		shipRunCmd.Flags().Set("skip-checks", fmt.Sprintf("%v", skipChecks))
		shipRunCmd.Flags().Set("approve", fmt.Sprintf("%v", approve))
		shipRunCmd.Flags().Set("format", format)
		
		return shipRunCmd.RunE(shipRunCmd, args)
	}
	shipCmd.Flags().StringP("message", "m", "", "Commit message (required)")
	shipCmd.Flags().Bool("dry-run", false, "Show what would happen without executing")
	shipCmd.Flags().Bool("skip-checks", false, "Skip quality gates")
	shipCmd.Flags().Bool("approve", false, "Auto-approve if confidence >= threshold")

	// Add commands
	shipCmd.AddCommand(shipRunCmd)

	// Add to root
	rootCmd.AddCommand(shipCmd)
}
