//go:build private
// +build private

package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// captureGitDescribe returns the git describe output (tags + dirty flag) from dir.
// Falls back to "dev" on any error.
func captureGitDescribe(dir string) string {
	out, err := exec.Command("git", "-C", dir, "describe", "--tags", "--always", "--dirty").Output()
	if err != nil || len(out) == 0 {
		return "dev"
	}
	return strings.TrimSpace(string(out))
}

// captureGitCommit returns the short HEAD commit hash from dir.
// Falls back to "unknown" on any error.
func captureGitCommit(dir string) string {
	out, err := exec.Command("git", "-C", dir, "rev-parse", "--short", "HEAD").Output()
	if err != nil || len(out) == 0 {
		return "unknown"
	}
	return strings.TrimSpace(string(out))
}

// findForgeOnPath returns the first 'forge' binary found on PATH.
func findForgeOnPath() (string, error) {
	pathEnv := os.Getenv("PATH")
	if pathEnv == "" {
		return "", fmt.Errorf("PATH environment variable is empty")
	}

	for _, dir := range strings.Split(pathEnv, string(os.PathListSeparator)) {
		candidate := filepath.Join(dir, "forge")
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			// Resolve symlinks to get the real path
			resolved, err := filepath.EvalSymlinks(candidate)
			if err != nil {
				continue
			}
			return resolved, nil
		}
	}
	return "", fmt.Errorf("forge binary not found on PATH")
}

// copyFile atomically replaces dst with src by using rename.
// This works around "text file busy" errors when replacing a running binary.
func copyFile(src, dst string) error {
	srcInfo, err := os.Stat(src)
	if err != nil {
		return err
	}

	// Read source file
	srcData, err := os.ReadFile(src)
	if err != nil {
		return err
	}

	// Write to temp file in same directory as dst (ensures same filesystem for rename)
	tmpDst := dst + ".tmp"
	if err := os.WriteFile(tmpDst, srcData, srcInfo.Mode()); err != nil {
		return err
	}

	// Try direct rename first (atomic on most systems)
	if err := os.Rename(tmpDst, dst); err != nil {
		// If rename fails (e.g., cross-device), try removing old and renaming again
		if removeErr := os.Remove(dst); removeErr != nil && !os.IsNotExist(removeErr) {
			os.Remove(tmpDst)
			return fmt.Errorf("could not remove old binary: %w", removeErr)
		}
		if renameErr := os.Rename(tmpDst, dst); renameErr != nil {
			os.Remove(tmpDst)
			return fmt.Errorf("could not install new binary: %w", renameErr)
		}
	}

	return nil
}

func init() {
	selfUpdateCmd.Flags().String("format", "table", "output format (table, json)")
}

var selfUpdateCmd = &cobra.Command{
	Use:   "self-update",
	Short: "Rebuild and reinstall the forge CLI binary",
	Long: `Rebuild the forge CLI from source and install to the first forge on PATH.

Steps:
1. Build forge binary from cmd/forge/ with version ldflags
2. Copy the binary to the first forge location found on PATH
3. Print the new version

FORGE_ROOT must be set or auto-detectable (a .forge/ directory in the tree).`,
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		formatter := internal.NewFormatter(format, nil)

		// Find FORGE_ROOT.
		forgeRoot := os.Getenv("FORGE_ROOT")
		if forgeRoot == "" {
			forgeRoot = findForgeRoot()
		}
		if forgeRoot == "" {
			return fmt.Errorf("FORGE_ROOT not set and could not be auto-detected\n" +
				"  Fix: export FORGE_ROOT=/path/to/forge-mono")
		}

		srcDir := filepath.Join(forgeRoot, "cmd", "forge")
		if _, err := os.Stat(srcDir); err != nil {
			return fmt.Errorf("source directory not found: %s\n  Is FORGE_ROOT correct?", srcDir)
		}

		// Step 1: Build to local forge file in cmd/forge/
		localBuild := filepath.Join(srcDir, "forge")
		formatter.Printf("Step 1: Building forge CLI...\n")
		formatter.Printf("  Source: %s\n", srcDir)

		// Capture version metadata to embed via ldflags.
		version := captureGitDescribe(srcDir)
		commit := captureGitCommit(srcDir)
		buildTime := time.Now().UTC().Format(time.RFC3339)
		ldflags := fmt.Sprintf(
			"-X main.Version=%s -X main.GitCommit=%s -X main.BuildTime=%s",
			version, commit, buildTime,
		)

		buildCmd := exec.Command("go", "build", "-tags", "private", "-ldflags", ldflags, "-o", "forge", ".")
		buildCmd.Dir = srcDir
		buildCmd.Stdout = os.Stdout
		buildCmd.Stderr = os.Stderr

		if err := buildCmd.Run(); err != nil {
			return fmt.Errorf("build failed: %w\n  Run manually: cd %s && go build -o forge .", err, srcDir)
		}
		formatter.Printf("  Built:  %s\n", localBuild)

		// Step 2: Find first forge on PATH and copy
		formatter.Printf("Step 2: Installing to PATH...\n")
		targetPath, err := findForgeOnPath()
		if err != nil {
			// Fall back to running binary path if not on PATH
			formatter.Printf("  Warning: %v\n", err)
			targetPath, err = os.Executable()
			if err != nil {
				return fmt.Errorf("could not determine target path: %w", err)
			}
			targetPath, err = filepath.EvalSymlinks(targetPath)
			if err != nil {
				return fmt.Errorf("could not resolve symlinks: %w", err)
			}
		}
		formatter.Printf("  Target: %s\n", targetPath)

		if err := copyFile(localBuild, targetPath); err != nil {
			return fmt.Errorf("failed to copy binary: %w", err)
		}
		formatter.Printf("  Installed!\n")

		// Step 3: Print new version
		formatter.Printf("Step 3: Verifying new version...\n")
		newVersionCmd := exec.Command(targetPath, "version")
		newVersionCmd.Stdout = os.Stdout
		newVersionCmd.Stderr = os.Stderr
		if err := newVersionCmd.Run(); err != nil {
			formatter.Printf("  Warning: could not run 'forge version': %v\n", err)
		}

		// Cleanup local build
		os.Remove(localBuild)

		if format == "json" {
			return formatter.WriteJSON(map[string]interface{}{
				"status":     "ok",
				"version":    version,
				"commit":     commit,
				"build_time": buildTime,
				"binary":     targetPath,
			})
		}

		formatter.Printf("\n✓ Self-update complete!\n")
		formatter.Printf("  Version: %s (commit: %s)\n", version, commit)
		formatter.Printf("  Binary:  %s\n", targetPath)
		return nil
	},
}
