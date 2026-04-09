//go:build !tmux_bridge

package main

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
)

// detectTailscaleIP returns this node's Tailscale IPv4 address if tailscale
// is installed and connected, or "" if unavailable.
// FORGE_TAILSCALE_IP env var overrides exec call — used in tests to avoid 2s timeout.
func detectTailscaleIP() string {
	if v, ok := os.LookupEnv("FORGE_TAILSCALE_IP"); ok {
		return v
	}
	out, err := exec.Command("tailscale", "ip", "-4").Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// minInt returns the smaller of two integers.
func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// priorityToInt converts a priority string to a numeric sort order.
// Lower numbers = higher priority. Unknown values default to 5.
func priorityToInt(p string) int {
	switch p {
	case "critical":
		return 1
	case "high":
		return 2
	case "medium":
		return 3
	case "low":
		return 4
	default:
		return 5
	}
}

// PopulateTasksFromFeatures is a stub — the implementation was removed as dead code.
// The populate_tasks.go file has been deleted. This stub keeps CLI commands compilable.
func PopulateTasksFromFeatures(dbPath, featuresPath string) error {
	return fmt.Errorf("populate-tasks command has been removed (dead code cleanup); use forge task create instead")
}

// PopulateTasksFromDispatches is a stub — the implementation was removed as dead code.
// The populate_dispatches.go file has been deleted. This stub keeps CLI commands compilable.
func PopulateTasksFromDispatches(dbPath, dispatchesDir string) error {
	return fmt.Errorf("import-dispatches command has been removed (dead code cleanup); use forge task create instead")
}
