//go:build private
// +build private

package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

var selfUpdateCmd = &cobra.Command{
	Use:   "self-update",
	Short: "Rebuild and reinstall the forge CLI binary",
	Long: `Rebuild the forge CLI from source and replace the current binary.

The build output is written directly to the path of the currently running
binary (resolved through symlinks), so the installed copy is updated in place.

FORGE_ROOT must be set or auto-detectable (a .forge/ directory in the tree).`,
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		formatter := internal.NewFormatter(format, nil)

		// Resolve the real path of the running binary (follow symlinks).
		exe, err := os.Executable()
		if err != nil {
			return fmt.Errorf("could not determine current executable path: %w", err)
		}
		exe, err = filepath.EvalSymlinks(exe)
		if err != nil {
			return fmt.Errorf("could not resolve symlinks for %s: %w", exe, err)
		}

		// Capture old mtime before overwriting.
		var oldMtime time.Time
		if info, err := os.Stat(exe); err == nil {
			oldMtime = info.ModTime()
		}

		// Find FORGE_ROOT.
		forgeRoot := os.Getenv("FORGE_ROOT")
		if forgeRoot == "" {
			forgeRoot = findForgeRoot()
		}
		if forgeRoot == "" {
			return fmt.Errorf("FORGE_ROOT not set and could not be auto-detected\n" +
				"  Fix: export FORGE_ROOT=/path/to/FORGE")
		}

		srcDir := filepath.Join(forgeRoot, "cmd", "forge")
		if _, err := os.Stat(srcDir); err != nil {
			return fmt.Errorf("source directory not found: %s\n  Is FORGE_ROOT correct?", srcDir)
		}

		formatter.Printf("Building %s → %s\n", srcDir, exe)

		buildCmd := exec.Command("go", "build", "-o", exe, ".")
		buildCmd.Dir = srcDir
		buildCmd.Stdout = os.Stdout
		buildCmd.Stderr = os.Stderr

		if err := buildCmd.Run(); err != nil {
			return fmt.Errorf("build failed: %w\n  Run manually: cd %s && go build ./...", err, srcDir)
		}

		// Report new mtime.
		var newMtime time.Time
		if info, err := os.Stat(exe); err == nil {
			newMtime = info.ModTime()
		}

		if format == "json" {
			return formatter.WriteJSON(map[string]interface{}{
				"status":    "ok",
				"binary":    exe,
				"old_mtime": oldMtime.Format(time.RFC3339),
				"new_mtime": newMtime.Format(time.RFC3339),
			})
		}

		formatter.Println("Build OK")
		formatter.Printf("  Binary:    %s\n", exe)
		if !oldMtime.IsZero() {
			formatter.Printf("  Old mtime: %s\n", oldMtime.Format("2006-01-02 15:04:05"))
		}
		if !newMtime.IsZero() {
			formatter.Printf("  New mtime: %s\n", newMtime.Format("2006-01-02 15:04:05"))
		}
		return nil
	},
}
