package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/spf13/cobra"
)

var controlPlaneCmd = &cobra.Command{
	Use:     "control-plane",
	Aliases: []string{"cp", "server"},
	Short:   "Manage the FORGE control plane",
	Long:    `Start, stop, and manage the FORGE control plane server that coordinates all workers and agents.`,
}

func init() {
	controlPlaneCmd.AddCommand(cpUpCmd)
	controlPlaneCmd.AddCommand(cpDownCmd)
	controlPlaneCmd.AddCommand(cpStatusCmd)
	controlPlaneCmd.AddCommand(cpLogsCmd)
}

var cpUpCmd = &cobra.Command{
	Use:   "up",
	Short: "Start the control plane server",
	Long:  `Start the control plane server in the background or foreground.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		foreground, _ := cmd.Flags().GetBool("foreground")
		detach, _ := cmd.Flags().GetBool("detach")

		if cfg == nil {
			return fmt.Errorf("configuration not loaded")
		}

		addr := fmt.Sprintf("%s:%d", cfg.ControlPlane.Host, cfg.ControlPlane.Port)
		fmt.Printf("Starting control plane on %s...\n", addr)

		// Check if already running
		if isControlPlaneRunning() {
			return fmt.Errorf("control plane is already running")
		}

		// Create data directories
		dataDir := cfg.ControlPlane.DataDir
		if !filepath.IsAbs(dataDir) {
			forgeRoot := os.Getenv("FORGE_ROOT")
			if forgeRoot == "" {
				forgeRoot = findForgeRoot()
			}
			if forgeRoot != "" {
				dataDir = filepath.Join(forgeRoot, dataDir)
			}
		}

		dirs := []string{
			dataDir,
			filepath.Join(dataDir, "xnode"),
			filepath.Join(dataDir, "xnode", "lead-inbox"),
			filepath.Join(dataDir, "xnode", "acks"),
			filepath.Join(dataDir, "xnode", "exceptions"),
		}

		for _, dir := range dirs {
			if err := os.MkdirAll(dir, 0755); err != nil {
				return fmt.Errorf("failed to create directory %s: %w", dir, err)
			}
		}

		// In foreground mode, run directly
		if foreground {
			return runControlPlane(cmd.Context())
		}

		// In detached mode, start in background
		if detach {
			return startControlPlaneBackground()
		}

		// Default: start in foreground with graceful shutdown
		return runControlPlaneWithGracefulShutdown()
	},
}

var cpDownCmd = &cobra.Command{
	Use:   "down",
	Short: "Stop the control plane server",
	Long:  `Stop the running control plane server gracefully.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		if !isControlPlaneRunning() {
			fmt.Println("Control plane is not running")
			return nil
		}

		fmt.Println("Stopping control plane...")

		// Try to read PID from file
		pidFile := getPIDFile()
		data, err := os.ReadFile(pidFile)
		if err == nil {
			var pid int
			if _, err := fmt.Sscanf(string(data), "%d", &pid); err == nil && pid > 0 {
				if err := syscall.Kill(pid, syscall.SIGTERM); err != nil {
					// Try SIGKILL if SIGTERM fails
					syscall.Kill(pid, syscall.SIGKILL)
				}
			}
		}

		// Clean up PID file
		os.Remove(pidFile)

		fmt.Println("Control plane stopped")
		return nil
	},
}

var cpStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Check control plane status",
	Long:  `Check if the control plane is running and display basic information.`,
	Run: func(cmd *cobra.Command, args []string) {
		if isControlPlaneRunning() {
			fmt.Println("Status: running")
			if cfg != nil {
				fmt.Printf("Address: %s:%d\n", cfg.ControlPlane.Host, cfg.ControlPlane.Port)
				fmt.Printf("Database: %s\n", cfg.ControlPlane.Database)
			}
		} else {
			fmt.Println("Status: stopped")
		}
	},
}

var cpLogsCmd = &cobra.Command{
	Use:   "logs",
	Short: "View control plane logs",
	Long:  `Stream or view the control plane server logs.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		follow, _ := cmd.Flags().GetBool("follow")
		tail, _ := cmd.Flags().GetInt("tail")

		logFile := getLogFile()

		if _, err := os.Stat(logFile); os.IsNotExist(err) {
			return fmt.Errorf("log file not found: %s", logFile)
		}

		var cmdArgs []string
		if follow {
			cmdArgs = append(cmdArgs, "-f")
		}
		if tail > 0 {
			cmdArgs = append(cmdArgs, fmt.Sprintf("-n %d", tail))
		}
		cmdArgs = append(cmdArgs, logFile)

		execCmd := exec.Command("tail", cmdArgs...)
		execCmd.Stdout = os.Stdout
		execCmd.Stderr = os.Stderr
		return execCmd.Run()
	},
}

func init() {
	cpUpCmd.Flags().BoolP("foreground", "f", false, "Run in foreground")
	cpUpCmd.Flags().BoolP("detach", "d", false, "Run detached in background")

	cpLogsCmd.Flags().BoolP("follow", "f", false, "Follow log output")
	cpLogsCmd.Flags().IntP("tail", "n", 100, "Number of lines to show from end")
}

func isControlPlaneRunning() bool {
	pidFile := getPIDFile()
	data, err := os.ReadFile(pidFile)
	if err != nil {
		return false
	}

	var pid int
	if _, err := fmt.Sscanf(string(data), "%d", &pid); err != nil || pid <= 0 {
		return false
	}

	// Check if process exists
	return syscall.Kill(pid, 0) == nil
}

func getPIDFile() string {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = findForgeRoot()
	}
	if forgeRoot == "" {
		return ".forge/control-plane.pid"
	}
	return filepath.Join(forgeRoot, ".forge", "control-plane.pid")
}

func getLogFile() string {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = findForgeRoot()
	}
	if forgeRoot == "" {
		return ".forge/control-plane.log"
	}
	return filepath.Join(forgeRoot, ".forge", "logs", "control-plane.log")
}

func runControlPlane(ctx context.Context) error {
	fmt.Printf("Control plane starting on %s:%d...\n", cfg.ControlPlane.Host, cfg.ControlPlane.Port)
	fmt.Println("Press Ctrl+C to stop")

	// This is a placeholder - in the real implementation, this would start
	// the actual control plane server from forge-v3
	fmt.Println("NOTE: Full control plane implementation available in forge-v3")

	<-ctx.Done()
	return ctx.Err()
}

func runControlPlaneWithGracefulShutdown() error {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Handle interrupt signals
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		<-sigChan
		fmt.Println("\nShutting down control plane...")
		cancel()
	}()

	return runControlPlane(ctx)
}

func startControlPlaneBackground() error {
	logFile := getLogFile()
	if err := os.MkdirAll(filepath.Dir(logFile), 0755); err != nil {
		return fmt.Errorf("failed to create log directory: %w", err)
	}

	f, err := os.OpenFile(logFile, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return fmt.Errorf("failed to open log file: %w", err)
	}
	defer f.Close()

	// Get the path to this binary
	exe, err := os.Executable()
	if err != nil {
		return fmt.Errorf("failed to get executable path: %w", err)
	}

	cmd := exec.Command(exe, "control-plane", "up", "--foreground")
	cmd.Stdout = f
	cmd.Stderr = f
	cmd.SysProcAttr = &syscall.SysProcAttr{
		Setsid: true,
	}

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("failed to start control plane: %w", err)
	}

	// Write PID file
	pidFile := getPIDFile()
	if err := os.WriteFile(pidFile, []byte(fmt.Sprintf("%d", cmd.Process.Pid)), 0644); err != nil {
		cmd.Process.Kill()
		return fmt.Errorf("failed to write PID file: %w", err)
	}

	fmt.Printf("Control plane started in background (PID: %d)\n", cmd.Process.Pid)
	fmt.Printf("Logs: %s\n", logFile)

	// Don't wait - detach
	go func() {
		cmd.Wait()
		os.Remove(pidFile)
	}()

	// Give it a moment to start
	time.Sleep(500 * time.Millisecond)
	return nil
}
