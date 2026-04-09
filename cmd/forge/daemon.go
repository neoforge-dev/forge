package main

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// daemonCmd represents the daemon workflow command
var daemonCmd = &cobra.Command{
	Use:     "daemon",
	Aliases: []string{"dm"},
	Short:   "Manage the FORGE daemon",
	Long:    "Start, stop, and check status of the FORGE daemon.",
}

// isPortListening checks if a port is listening on localhost
func isPortListening(port string) bool {
	conn, err := net.DialTimeout("tcp", "localhost:"+port, time.Second)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

// forgeDBPath returns the canonical path to the SQLite database.
// NOTE: The DB filename stays forge-v3.db intentionally — renaming it would
// break live nodes that already have data at this path.
func forgeDBPath() string {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	return filepath.Join(root, ".forge", "forge-v3.db")
}

// pidFilePath returns the path to the PID file
func pidFilePath() string {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	return filepath.Join(root, ".forge", "forged.pid")
}

// readPIDFile reads the PID from the PID file
func readPIDFile() (int, error) {
	data, err := os.ReadFile(pidFilePath())
	if err != nil {
		return 0, err
	}
	return strconv.Atoi(strings.TrimSpace(string(data)))
}

// writePIDFile writes the PID to the PID file
func writePIDFile(pid int) error {
	pidPath := pidFilePath()
	// Ensure .forge directory exists
	if err := os.MkdirAll(filepath.Dir(pidPath), 0755); err != nil {
		return err
	}
	return os.WriteFile(pidPath, []byte(strconv.Itoa(pid)+"\n"), 0644)
}

// removePIDFile removes the PID file
func removePIDFile() error {
	return os.Remove(pidFilePath())
}

// daemonEnv returns the environment for the forged child process.
// It inherits the current environment and overlays NODE_ID and FORGE_LEAD_URL
// from the forge config so operators don't need to pre-set shell vars.
func daemonEnv() []string {
	env := os.Environ()

	// NODE_ID: prefer existing env var, then hostname
	if os.Getenv("NODE_ID") == "" {
		if hostname, err := os.Hostname(); err == nil {
			env = append(env, "NODE_ID="+hostname)
		}
	}

	// FORGE_LEAD_URL: prefer existing env var, then [control_plane].url from config
	if os.Getenv("FORGE_LEAD_URL") == "" {
		// Read from forge.toml / forge.yaml config via viper if available.
		// Fallback: check FORGE_LEAD_URL env written by forge init.
		if cfgURL := getConfigLeadURL(); cfgURL != "" {
			env = append(env, "FORGE_LEAD_URL="+cfgURL)
		}
	}
	return env
}

// getConfigLeadURL returns the control_plane.url from the forge config file.
func getConfigLeadURL() string {
	// Look for config in well-known locations
	candidates := []string{
		filepath.Join(os.Getenv("HOME"), ".forge", "config.toml"),
		".forge/forge.toml",
		".forge/forge.yaml",
	}
	for _, p := range candidates {
		data, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		// Simple TOML parse: look for url = "..."  under [control_plane]
		inSection := false
		for _, line := range strings.Split(string(data), "\n") {
			line = strings.TrimSpace(line)
			if line == "[control_plane]" {
				inSection = true
				continue
			}
			if strings.HasPrefix(line, "[") {
				inSection = false
			}
			if inSection && strings.HasPrefix(line, "url") {
				parts := strings.SplitN(line, "=", 2)
				if len(parts) == 2 {
					url := strings.Trim(strings.TrimSpace(parts[1]), `"'`)
					if url != "" {
						return url
					}
				}
			}
		}
	}
	return ""
}

// findDaemonBinary finds the forged binary (with forge-v3 fallback for backward compat)
func findDaemonBinary() string {
	// Check FORGED_BIN environment variable first (new name)
	if v := os.Getenv("FORGED_BIN"); v != "" {
		if _, err := os.Stat(v); err == nil {
			return v
		}
	}
	// Backward compat: also accept FORGE_V3_BIN
	if v := os.Getenv("FORGE_V3_BIN"); v != "" {
		if _, err := os.Stat(v); err == nil {
			return v
		}
	}

	// Look next to the forge binary
	exe, err := os.Executable()
	if err == nil {
		exeDir := filepath.Dir(exe)
		// Try relative to forge binary location — forged first, then forge-v3 fallback
		candidates := []string{
			filepath.Join(exeDir, "..", "forged", "forged"),
			filepath.Join(exeDir, "..", "..", "forged", "forged"),
			filepath.Join(exeDir, "forged"),
			// Backward compat fallback
			filepath.Join(exeDir, "..", "forge-v3", "forge-v3"),
			filepath.Join(exeDir, "..", "..", "forge-v3", "forge-v3"),
			filepath.Join(exeDir, "forge-v3"),
		}
		for _, candidate := range candidates {
			if absPath, err := filepath.Abs(candidate); err == nil {
				if _, err := os.Stat(absPath); err == nil {
					return absPath
				}
			}
		}
	}

	// Try FORGE_ROOT
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot != "" {
		// forged first, then forge-v3 fallback
		for _, rel := range []string{
			filepath.Join("cmd", "forged", "forged"),
			filepath.Join("cmd", "forge-v3", "forge-v3"),
		} {
			candidate := filepath.Join(forgeRoot, rel)
			if _, err := os.Stat(candidate); err == nil {
				return candidate
			}
		}
	}

	// Try PATH — forged first, then forge-v3 fallback
	if runtime.GOOS == "windows" {
		if path, err := exec.LookPath("forged.exe"); err == nil {
			return path
		}
		if path, err := exec.LookPath("forge-v3.exe"); err == nil {
			return path
		}
	} else {
		if path, err := exec.LookPath("forged"); err == nil {
			return path
		}
		if path, err := exec.LookPath("forge-v3"); err == nil {
			return path
		}
	}

	// Try relative to current working directory
	if cwd, err := os.Getwd(); err == nil {
		for _, rel := range []string{
			filepath.Join("cmd", "forged", "forged"),
			filepath.Join("cmd", "forge-v3", "forge-v3"),
		} {
			candidate := filepath.Join(cwd, rel)
			if _, err := os.Stat(candidate); err == nil {
				return candidate
			}
		}
	}

	return ""
}

// getPIDFromPort finds the PID of the process listening on a port
func getPIDFromPort(port string) (int, error) {
	var cmd *exec.Cmd
	if runtime.GOOS == "darwin" {
		cmd = exec.Command("lsof", "-ti", ":"+port)
	} else if runtime.GOOS == "linux" {
		cmd = exec.Command("ss", "-tlnp", "sport", ":"+port)
		// Alternative using lsof
		cmd = exec.Command("lsof", "-ti", ":"+port)
	} else {
		return 0, fmt.Errorf("unsupported OS: %s", runtime.GOOS)
	}

	output, err := cmd.Output()
	if err != nil {
		return 0, err
	}

	pidStr := strings.TrimSpace(string(output))
	if pidStr == "" {
		return 0, fmt.Errorf("no process found on port %s", port)
	}

	// Handle multiple PIDs (take first)
	pidStr = strings.Split(pidStr, "\n")[0]
	return strconv.Atoi(pidStr)
}

// daemonStartCmd: forge daemon start
var daemonStartCmd = &cobra.Command{
	Use:   "start",
	Short: "Start the FORGE daemon",
	Long:  fmt.Sprintf("Start the FORGE daemon in the background on port %s with WebSocket on %s.", internal.ResolveAPIPort(), internal.ResolveWSPort()),
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		foreground, _ := cmd.Flags().GetBool("foreground")

		apiPort := internal.ResolveAPIPort()
		wsPort := internal.ResolveWSPort()

		// Check if already running on the API port
		if isPortListening(apiPort) {
			// Try to get PID from file or port
			pid, _ := readPIDFile()
			if pid == 0 {
				pid, _ = getPIDFromPort(apiPort)
			}
			formatter := internal.NewFormatter(format, nil)
			if pid > 0 {
				formatter.Printf("forged already running (pid %d)\n", pid)
			} else {
				formatter.Println("forged already running")
			}
			return nil
		}

		// Find the forged binary
		binaryPath := findDaemonBinary()
		if binaryPath == "" {
			return fmt.Errorf("forged binary not found (set FORGED_BIN or ensure it's in PATH)")
		}

		if foreground {
			// Run in foreground
			formatter := internal.NewFormatter(format, nil)
			formatter.Println("Starting forged in foreground...")
			formatter.Println("Press Ctrl+C to stop")

			execCmd := exec.Command(binaryPath, "--port", apiPort, "--ws-port", wsPort, "--db", forgeDBPath())
			execCmd.Stdout = os.Stdout
			execCmd.Stderr = os.Stderr
			return execCmd.Run()
		}

		// Start in background with log file
		homeDir, err := os.UserHomeDir()
		if err != nil {
			homeDir = "/tmp"
		}
		logDir := filepath.Join(homeDir, ".forge", "logs")
		os.MkdirAll(logDir, 0755) //nolint:errcheck
		logFile := filepath.Join(logDir, "v3-daemon.log")
		f, err := os.OpenFile(logFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err != nil {
			return fmt.Errorf("failed to open log file: %w", err)
		}
		defer f.Close()

		execCmd := exec.Command(binaryPath, "--port", apiPort, "--ws-port", wsPort, "--db", forgeDBPath())
		execCmd.Stdout = f
		execCmd.Stderr = f
		execCmd.SysProcAttr = &syscall.SysProcAttr{
			Setpgid: true,
		}
		// Inject node identity env vars from forge config so the daemon
		// knows its NODE_ID and FORGE_LEAD_URL without requiring the user
		// to set them in the shell environment.
		execCmd.Env = daemonEnv()

		if err := execCmd.Start(); err != nil {
			return fmt.Errorf("failed to start forged: %w", err)
		}

		pid := execCmd.Process.Pid

		// Poll until API port responds (max 5s)
		started := false
		for i := 0; i < 50; i++ {
			time.Sleep(100 * time.Millisecond)
			if isPortListening(apiPort) {
				started = true
				break
			}
		}

		if !started {
			// Kill the process if it didn't start properly
			execCmd.Process.Kill()
			return fmt.Errorf("forged failed to start within 5 seconds (check %s)", logFile)
		}

		// Write PID file
		if err := writePIDFile(pid); err != nil {
			// Non-fatal - daemon is running
			fmt.Fprintf(os.Stderr, "Warning: could not write PID file: %v\n", err)
		}

		formatter := internal.NewFormatter(format, nil)
		formatter.Printf("forged started (pid %d)\n", pid)
		return nil
	},
}

// daemonStopCmd: forge daemon stop
var daemonStopCmd = &cobra.Command{
	Use:   "stop",
	Short: "Stop the FORGE daemon",
	Long:  "Stop the running FORGE daemon gracefully.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		force, _ := cmd.Flags().GetBool("force")

		apiPort := internal.ResolveAPIPort()

		// Check if running
		if !isPortListening(apiPort) {
			formatter := internal.NewFormatter(format, nil)
			formatter.Println("forged is not running")
			removePIDFile() // Clean up stale PID file
			return nil
		}

		// Get PID from file or port
		pid, err := readPIDFile()
		if err != nil {
			pid, err = getPIDFromPort(apiPort)
			if err != nil {
				return fmt.Errorf("could not find forged process: %w", err)
			}
		}

		// Send signal
		sig := syscall.SIGTERM
		if force {
			sig = syscall.SIGKILL
		}

		if err := syscall.Kill(pid, sig); err != nil {
			// Try to find process again (PID might have changed)
			pid, err = getPIDFromPort(apiPort)
			if err != nil {
				return fmt.Errorf("could not find forged process: %w", err)
			}
			if err := syscall.Kill(pid, sig); err != nil {
				return fmt.Errorf("failed to stop forged: %w", err)
			}
		}

		// Wait up to 5s for port to free
		stopped := false
		for i := 0; i < 50; i++ {
			time.Sleep(100 * time.Millisecond)
			if !isPortListening(apiPort) {
				stopped = true
				break
			}
		}

		formatter := internal.NewFormatter(format, nil)
		if stopped {
			removePIDFile()
			formatter.Println("forged stopped")
		} else if !force {
			// Try force kill
			syscall.Kill(pid, syscall.SIGKILL)
			time.Sleep(500 * time.Millisecond)
			if !isPortListening(apiPort) {
				removePIDFile()
				formatter.Println("forged stopped (forced)")
			} else {
				return fmt.Errorf("failed to stop forged (pid %d)", pid)
			}
		} else {
			return fmt.Errorf("failed to stop forged (pid %d)", pid)
		}

		return nil
	},
}

// daemonStatusCmd: forge daemon status
var daemonStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Check daemon status",
	Long:  "Check the status of the FORGE daemon.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		formatter := internal.NewFormatter(format, nil)

		apiPort := internal.ResolveAPIPort()
		wsPort := internal.ResolveWSPort()

		// Check if API port is listening
		apiRunning := isPortListening(apiPort)
		wsRunning := isPortListening(wsPort)

		if !apiRunning {
			if format == "json" {
				// JSON output: write status and return error so exit code is 1.
				_ = formatter.WriteJSON(map[string]string{
					"status": "not_running",
				})
				return fmt.Errorf("forged not running")
			}
			if format == "quiet" {
				os.Exit(3)
			}
			formatter.Println("forged DOWN")
			fmt.Fprintf(os.Stderr, "\n  Recovery: forge daemon start\n           OR check: forge daemon logs\n")
			return fmt.Errorf("forged not running on port %s", apiPort)
		}

		// Get PID — verify file PID is live, fall back to port scan
		pid, _ := readPIDFile()
		if pid > 0 {
			// Verify process exists (signal 0 = existence check)
			if err := syscall.Kill(pid, 0); err != nil {
				pid = 0 // stale PID file
			}
		}
		if pid == 0 {
			pid, _ = getPIDFromPort(apiPort)
			if pid > 0 {
				// Update stale PID file
				_ = writePIDFile(pid)
			}
		}

		// Try to get task count from API
		taskCount := 0
		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()

		resp, err := client.Get(ctx, "/tasks?limit=1")
		if err == nil && resp.StatusCode == http.StatusOK {
			body, _ := io.ReadAll(resp.Body)
			resp.Body.Close()
			// Simple string search for count
			if idx := strings.Index(string(body), `"count":`); idx != -1 {
				rest := body[idx+8:]
				if endIdx := strings.IndexAny(string(rest), ",}"); endIdx != -1 {
					taskCount, _ = strconv.Atoi(strings.TrimSpace(string(rest[:endIdx])))
				}
			}
		}

		if format == "json" {
			return formatter.WriteJSON(map[string]interface{}{
				"status":      "running",
				"pid":         pid,
				"api_port":    8081,
				"ws_port":     8082,
				"ws_running":  wsRunning,
				"task_count":  taskCount,
			})
		}

		if format == "quiet" {
			return nil
		}

		formatter.Println("forged RUNNING")
		if pid > 0 {
			formatter.Printf("  PID: %d\n", pid)
		}
		formatter.Printf("  API: localhost:%s\n", apiPort)
		if wsRunning {
			formatter.Printf("  WebSocket: localhost:%s\n", wsPort)
		} else {
			formatter.Printf("  WebSocket: not running\n")
		}
		formatter.Printf("  Tasks: %d\n", taskCount)
		if homeDir, err := os.UserHomeDir(); err == nil {
			formatter.Printf("  Log: %s\n", filepath.Join(homeDir, ".forge", "logs", "v3-daemon.log"))
		}

		return nil
	},
}

// daemonRestartCmd: forge daemon restart
var daemonRestartCmd = &cobra.Command{
	Use:   "restart",
	Short: "Rebuild (optional) and restart the FORGE daemon",
	Long: `Stop the running daemon, rebuild the binary from source (default), then start fresh.

By default the forged binary is rebuilt before restarting. Pass --skip-build to
reuse the existing binary. On build failure the restart is aborted so the running
daemon is never replaced with a stale or broken binary.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		skipBuild, _ := cmd.Flags().GetBool("skip-build")

		formatter := internal.NewFormatter(format, nil)

		// Determine FORGE_ROOT: env var → os.Getwd() fallback
		forgeRoot := os.Getenv("FORGE_ROOT")
		if forgeRoot == "" {
			if cwd, err := os.Getwd(); err == nil {
				forgeRoot = cwd
			}
		}

		// Step 1: Build (default on, skip with --skip-build)
		if !skipBuild {
			if forgeRoot == "" {
				return fmt.Errorf("[restart] FORGE_ROOT not set and os.Getwd() failed — cannot build (export FORGE_ROOT=/path/to/FORGE or pass --skip-build)")
			}

			srcDir := filepath.Join(forgeRoot, "cmd", "forged")
			if _, err := os.Stat(srcDir); os.IsNotExist(err) {
				return fmt.Errorf("[restart] Source directory %s does not exist — check FORGE_ROOT (currently %q)", srcDir, forgeRoot)
			}
			outBin := filepath.Join(srcDir, "forged")
			formatter.Printf("[restart] Building %s...\n", srcDir)

			// Capture version metadata to embed via ldflags.
			version := captureGitMetadata("describe", "--tags", "--always", "--dirty")
			if version == "" {
				version = "dev"
			}
			commit := captureGitMetadata("rev-parse", "--short", "HEAD")
			if commit == "" {
				commit = "unknown"
			}
			buildTime := time.Now().UTC().Format(time.RFC3339)
			ldflags := fmt.Sprintf(
				"-X main.Version=%s -X main.GitCommit=%s -X main.BuildTime=%s",
				version, commit, buildTime,
			)

			buildCmd := exec.Command("go", "build", "-ldflags", ldflags, "-o", outBin, ".")
			buildCmd.Dir = srcDir
			buildCmd.Stdout = os.Stdout
			buildCmd.Stderr = os.Stderr
			if err := buildCmd.Run(); err != nil {
				return fmt.Errorf("[restart] Build failed — daemon NOT restarted: %w", err)
			}
			formatter.Println("[restart] Build OK")
		}

		// Step 2: Stop if running
		formatter.Println("[restart] Stopping...")
		if isPortListening(internal.ResolveAPIPort()) {
			if err := daemonStopCmd.RunE(cmd, args); err != nil {
				return fmt.Errorf("[restart] Stop failed: %w", err)
			}
			time.Sleep(500 * time.Millisecond)
		} else {
			formatter.Println("[restart] (daemon was not running)")
		}

		// Step 3: Start
		formatter.Println("[restart] Starting...")
		if err := daemonStartCmd.RunE(cmd, args); err != nil {
			return fmt.Errorf("[restart] Start failed: %w", err)
		}

		formatter.Println("[restart] Done")
		return nil
	},
}

func init() {
	// daemon start flags
	daemonStartCmd.Flags().Bool("foreground", false, "Run daemon in foreground")

	// daemon stop flags
	daemonStopCmd.Flags().Bool("force", false, "Force stop (kill -9)")

	// daemon restart flags
	daemonRestartCmd.Flags().Bool("skip-build", false, "Skip rebuilding the forged binary (use existing binary)")
	daemonRestartCmd.Flags().Bool("rebuild", false, "Deprecated alias: rebuild is now the default; use --skip-build to disable")
	daemonRestartCmd.Flags().MarkHidden("rebuild") //nolint:errcheck

	// daemon install flags
	daemonInstallCmd.Flags().Bool("enable", false, "Enable service to start on boot")
	daemonInstallCmd.Flags().Bool("user", false, "Install as user service (no sudo required)")

	// daemon logs flags
	daemonLogsCmd.Flags().BoolP("follow", "f", false, "Stream log output continuously (tail -f)")
	daemonLogsCmd.Flags().IntP("lines", "n", 50, "Number of lines to show")

	// Add commands to daemon noun
	daemonCmd.AddCommand(daemonStartCmd)
	daemonCmd.AddCommand(daemonStopCmd)
	daemonCmd.AddCommand(daemonStatusCmd)
	daemonCmd.AddCommand(daemonRestartCmd)
	daemonCmd.AddCommand(daemonInstallCmd)
	daemonCmd.AddCommand(daemonLogsCmd)
}

// daemonLogsCmd: forge daemon logs
var daemonLogsCmd = &cobra.Command{
	Use:   "logs",
	Short: "View daemon log output",
	Long:  "Print the last N lines of the FORGE daemon log. Use --follow to stream continuously.",
	RunE: func(cmd *cobra.Command, args []string) error {
		follow, _ := cmd.Flags().GetBool("follow")
		lines, _ := cmd.Flags().GetInt("lines")

		logPath := findDaemonLogPath()
		if logPath == "" {
			return fmt.Errorf("daemon log not found (checked ~/.forge/logs/v3-daemon.log and /tmp/forged.log)")
		}

		linesStr := strconv.Itoa(lines)
		var tailCmd *exec.Cmd
		if follow {
			tailCmd = exec.Command("tail", "-f", "-n", linesStr, logPath)
		} else {
			tailCmd = exec.Command("tail", "-n", linesStr, logPath)
		}
		tailCmd.Stdout = os.Stdout
		tailCmd.Stderr = os.Stderr
		return tailCmd.Run()
	},
}

// findDaemonLogPath returns the first existing log file from the candidate list.
func findDaemonLogPath() string {
	homeDir, err := os.UserHomeDir()
	if err != nil {
		homeDir = ""
	}
	candidates := []string{}
	if homeDir != "" {
		candidates = append(candidates, filepath.Join(homeDir, ".forge", "logs", "v3-daemon.log"))
	}
	candidates = append(candidates, "/tmp/forged.log")
	// Backward compat: also check old log path
	candidates = append(candidates, "/tmp/forge-v3.log")

	for _, p := range candidates {
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return ""
}

// daemonInstallCmd: forge daemon install
var daemonInstallCmd = &cobra.Command{
	Use:   "install",
	Short: "Install daemon as a systemd service",
	Long:  "Generate and install a systemd unit file for forged. Use --enable to start on boot.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		enable, _ := cmd.Flags().GetBool("enable")
		user, _ := cmd.Flags().GetBool("user")

		formatter := internal.NewFormatter(format, nil)

		// Find the binary
		binaryPath := findDaemonBinary()
		if binaryPath == "" {
			return fmt.Errorf("forged binary not found (set FORGED_BIN or ensure it's in PATH)")
		}

		// Get absolute path to binary
		absBinary, err := filepath.Abs(binaryPath)
		if err != nil {
			return fmt.Errorf("failed to get absolute path: %w", err)
		}

		// Generate unit content
		var unitContent string
		var serviceName string
		var serviceFilePath string

		apiPort := internal.ResolveAPIPort()
		wsPort := internal.ResolveWSPort()

		if user {
			serviceName = "forged.service"
			unitContent = fmt.Sprintf(`[Unit]
Description=FORGE v3 Fleet Operations Engine
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=%s
WorkingDirectory=%s
ExecStart=%s --port %s --ws-port %s
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
`, os.Getenv("USER"), filepath.Dir(absBinary), absBinary, apiPort, wsPort)
			homeDir, _ := os.UserHomeDir()
			serviceFilePath = filepath.Join(homeDir, ".config", "systemd", "user", serviceName)
		} else {
			serviceName = "forged.service"
			unitContent = fmt.Sprintf(`[Unit]
Description=FORGE v3 Fleet Operations Engine
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=%s
ExecStart=%s --port %s --ws-port %s
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
`, filepath.Dir(absBinary), absBinary, apiPort, wsPort)
			serviceFilePath = "/etc/systemd/system/" + serviceName
		}

		// Write unit file
		if err := os.MkdirAll(filepath.Dir(serviceFilePath), 0755); err != nil {
			return fmt.Errorf("failed to create directory: %w", err)
		}

		if err := os.WriteFile(serviceFilePath, []byte(unitContent), 0644); err != nil {
			return fmt.Errorf("failed to write unit file: %w", err)
		}

		formatter.Printf("Installed: %s\n", serviceFilePath)

		// Reload systemd and enable if requested
		if !user {
			reloadCmd := exec.Command("systemctl", "daemon-reload")
			if err := reloadCmd.Run(); err != nil {
				formatter.Println("Warning: failed to reload systemd (may need sudo)")
			}
		}

		if enable {
			var enableCmd *exec.Cmd
			if user {
				enableCmd = exec.Command("systemctl", "--user", "enable", serviceName)
			} else {
				enableCmd = exec.Command("systemctl", "enable", serviceName)
			}
			if err := enableCmd.Run(); err != nil {
				return fmt.Errorf("failed to enable service: %w", err)
			}
			formatter.Printf("Enabled: %s (will start on boot)\n", serviceName)
		}

		formatter.Println("\nTo start manually:")
		if user {
			formatter.Printf("  systemctl --user start %s\n", serviceName)
		} else {
			formatter.Printf("  sudo systemctl start %s\n", serviceName)
		}

		return nil
	},
}

// captureGitMetadata runs a git command and returns trimmed stdout.
// Returns "" on any error so callers can substitute a default.
func captureGitMetadata(args ...string) string {
	out, err := exec.Command("git", args...).Output()
	if err != nil || len(out) == 0 {
		return ""
	}
	return strings.TrimSpace(string(out))
}
