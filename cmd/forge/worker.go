package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/gorilla/websocket"
	"github.com/spf13/cobra"
)

// WebSocketProtocolVersion is the protocol version for WebSocket communication.
// This must match the server's ProtocolVersion in cmd/forged/websocket.go
const WebSocketProtocolVersion = "1"

// wsEnvelope mirrors the v3 daemon WSMessage envelope.
type wsEnvelope struct {
	Version string          `json:"v"`
	Type    string          `json:"type"`
	ID      string          `json:"id"`
	TaskID  string          `json:"task_id,omitempty"`
	Payload json.RawMessage `json:"payload"`
	Time    time.Time       `json:"ts"`
}

// wsRegisterPayload is the payload for the agent.register handshake.
type wsRegisterPayload struct {
	AgentID      string   `json:"agent_id"`
	Name         string   `json:"name"`
	Node         string   `json:"node"`
	Tier         string   `json:"tier"`
	Capabilities []string `json:"capabilities"`
}

// wsMarshal marshals a value to json.RawMessage, returning an empty object on error.
func wsMarshal(v interface{}) json.RawMessage {
	data, err := json.Marshal(v)
	if err != nil {
		return json.RawMessage("{}")
	}
	return data
}

// deriveWSURL converts a control-plane HTTP URL to the v3 daemon WebSocket URL.
// The control plane HTTP API lives on one port (e.g. 8081); the WebSocket daemon
// always listens on WS_PORT (default 8082) at path /ws.
func deriveWSURL(controlPlane string) string {
	wsPort := os.Getenv("WS_PORT")
	if wsPort == "" {
		wsPort = "8082"
	}
	return fmt.Sprintf("ws://localhost:%s/ws", wsPort)
}

var workerCmd = &cobra.Command{
	Use:   "worker",
	Short: "Manage FORGE worker nodes",
	Long:  `Start, stop, and manage worker nodes that connect to the control plane.`,
}

func init() {
	workerCmd.AddCommand(workerUpCmd)
	workerCmd.AddCommand(workerDownCmd)
	workerCmd.AddCommand(workerStatusCmd)
	workerCmd.AddCommand(workerListCmd)
}

var workerUpCmd = &cobra.Command{
	Use:   "up",
	Short: "Start a worker node",
	Long:  `Start a worker node that connects to the control plane.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		foreground, _ := cmd.Flags().GetBool("foreground")
		workerID, _ := cmd.Flags().GetString("id")
		controlPlaneURL, _ := cmd.Flags().GetString("control-plane")
		capabilities, _ := cmd.Flags().GetStringSlice("capabilities")
		maxConcurrent, _ := cmd.Flags().GetInt("max-concurrent")

		if workerID == "" {
			// Default to hostname for stable identity across restarts
			workerID, _ = os.Hostname()
			if workerID == "" {
				workerID = fmt.Sprintf("worker-%d", time.Now().Unix())
			}
		}

		// Determine control plane URL
		if controlPlaneURL == "" {
			if cfg != nil && cfg.Worker.ControlPlane != "" {
				controlPlaneURL = cfg.Worker.ControlPlane
			} else if cfg != nil {
				controlPlaneURL = fmt.Sprintf("http://%s:%d", cfg.ControlPlane.Host, cfg.ControlPlane.Port)
			} else {
				controlPlaneURL = "http://localhost:8080"
			}
		}

		fmt.Printf("Starting worker %s...\n", workerID)
		fmt.Printf("Control plane: %s\n", controlPlaneURL)

		// Check if already running
		if isWorkerRunning(workerID) {
			return fmt.Errorf("worker %s is already running", workerID)
		}

		// Prepare worker config
		workerCfg := WorkerConfig{
			ID:            workerID,
			ControlPlane:  controlPlaneURL,
			Capabilities:  capabilities,
			MaxConcurrent: maxConcurrent,
		}

		// In foreground mode, run directly
		if foreground {
			return runWorker(cmd.Context(), workerCfg)
		}

		// Run in background
		return startWorkerBackground(workerCfg)
	},
}

var workerDownCmd = &cobra.Command{
	Use:   "down [worker-id]",
	Short: "Stop a worker node",
	Long:  `Stop a running worker node. If no worker-id is provided, stops all local workers.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		all, _ := cmd.Flags().GetBool("all")

		if all || len(args) == 0 {
			// Stop all workers
			workers, err := listRunningWorkers()
			if err != nil {
				return fmt.Errorf("failed to list workers: %w", err)
			}

			if len(workers) == 0 {
				fmt.Println("No workers running")
				return nil
			}

			for _, workerID := range workers {
				fmt.Printf("Stopping worker %s...\n", workerID)
				if err := stopWorker(workerID); err != nil {
					fmt.Fprintf(os.Stderr, "Failed to stop worker %s: %v\n", workerID, err)
				}
			}
			return nil
		}

		// Stop specific worker
		workerID := args[0]
		fmt.Printf("Stopping worker %s...\n", workerID)
		return stopWorker(workerID)
	},
}

type localWorkerStatus struct {
	hasPIDFile bool
	pid        int
	running    bool
	reason     string
}

func getLocalWorkerStatus(workerID string) localWorkerStatus {
	pidFile := getWorkerPIDFile(workerID)
	data, err := os.ReadFile(pidFile)
	if err != nil {
		if os.IsNotExist(err) {
			return localWorkerStatus{hasPIDFile: false, running: false, reason: "no PID file"}
		}
		return localWorkerStatus{hasPIDFile: false, running: false, reason: "PID file unreadable"}
	}

	var pid int
	if _, err := fmt.Sscanf(string(data), "%d", &pid); err != nil || pid <= 0 {
		return localWorkerStatus{hasPIDFile: true, pid: 0, running: false, reason: "invalid PID file"}
	}

	if syscall.Kill(pid, 0) == nil {
		return localWorkerStatus{hasPIDFile: true, pid: pid, running: true}
	}
	return localWorkerStatus{hasPIDFile: true, pid: pid, running: false, reason: "stale PID file"}
}

func listLocalWorkerIDs() ([]string, error) {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = findForgeRoot()
	}

	var workersDir string
	if forgeRoot != "" {
		workersDir = filepath.Join(forgeRoot, ".forge", "workers")
	} else {
		workersDir = ".forge/workers"
	}

	entries, err := os.ReadDir(workersDir)
	if err != nil {
		if os.IsNotExist(err) {
			return []string{}, nil
		}
		return nil, err
	}

	ids := make([]string, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		if filepath.Ext(entry.Name()) != ".pid" {
			continue
		}
		ids = append(ids, strings.TrimSuffix(entry.Name(), ".pid"))
	}
	sort.Strings(ids)
	return ids, nil
}

func formatAgo(t time.Time) string {
	if t.IsZero() {
		return "unknown"
	}

	d := time.Since(t)
	if d < 0 {
		d = 0
	}

	secs := int(d.Seconds())
	switch {
	case secs < 60:
		return fmt.Sprintf("%ds ago", secs)
	case secs < 3600:
		return fmt.Sprintf("%dm ago", secs/60)
	case secs < 86400:
		return fmt.Sprintf("%dh ago", secs/3600)
	default:
		return fmt.Sprintf("%dd ago", secs/86400)
	}
}

var workerStatusCmd = &cobra.Command{
	Use:   "status [worker-id]",
	Short: "Check worker status",
	Long:  `Check the status of a worker node. If no worker-id is provided, shows status of all workers.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := cmd.Context()
		client := internal.NewClient()

		if len(args) == 0 {
			localIDs, err := listLocalWorkerIDs()
			if err != nil {
				return fmt.Errorf("failed to list local workers: %w", err)
			}

			daemonAgents, daemonErr := client.ListAgents(ctx)
			daemonByID := map[string]internal.Agent{}
			if daemonErr == nil && daemonAgents != nil {
				for _, a := range daemonAgents.Agents {
					daemonByID[a.ID] = a
				}
			}

			union := map[string]struct{}{}
			for _, id := range localIDs {
				union[id] = struct{}{}
			}
			for id := range daemonByID {
				union[id] = struct{}{}
			}

			if len(union) == 0 {
				if daemonErr != nil {
					fmt.Println("No local workers found (daemon unreachable)")
				} else {
					fmt.Println("No workers found")
				}
				return nil
			}

			ids := make([]string, 0, len(union))
			for id := range union {
				ids = append(ids, id)
			}
			sort.Strings(ids)

			for _, workerID := range ids {
				local := getLocalWorkerStatus(workerID)

				fmt.Printf("Worker: %s\n", workerID)
				if local.running {
					fmt.Printf("  Local:  running (PID %d)\n", local.pid)
				} else if local.hasPIDFile {
					fmt.Printf("  Local:  stopped (%s)\n", local.reason)
				} else {
					fmt.Printf("  Local:  stopped (no PID file)\n")
				}

				if daemonErr != nil {
					fmt.Printf("  Daemon: offline (daemon unreachable)\n\n")
					continue
				}

				a, ok := daemonByID[workerID]
				if !ok {
					fmt.Printf("  Daemon: offline\n\n")
					continue
				}

				status := a.Status
				if status == "" {
					status = "unknown"
				}
				fmt.Printf("  Daemon: %s (node: %s, last_seen: %s)\n\n", status, a.Node, formatAgo(a.LastPong))
			}

			return nil
		}

		workerID := args[0]
		local := getLocalWorkerStatus(workerID)

		localLabel := "stopped"
		if local.running {
			localLabel = "running"
		}

		health, err := client.GetAgent(ctx, workerID)
		if err != nil {
			var unreachable *internal.DaemonUnreachableError
			if errors.As(err, &unreachable) {
				fmt.Printf("%s: %s | daemon: offline (daemon unreachable)\n", workerID, localLabel)
				return nil
			}
			fmt.Printf("%s: %s | daemon: offline\n", workerID, localLabel)
			return nil
		}

		daemonStatus := health.Status
		if daemonStatus == "" {
			daemonStatus = "unknown"
		}
		fmt.Printf("%s: %s | daemon: %s (last seen %s)\n", workerID, localLabel, daemonStatus, formatAgo(health.LastSeen))
		return nil
	},
}

var workerListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all workers",
	Long:  `List all worker nodes registered with the control plane.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := cmd.Context()
		client := internal.NewClient()

		localIDs, err := listLocalWorkerIDs()
		if err != nil {
			return fmt.Errorf("failed to list local workers: %w", err)
		}

		daemonAgents, daemonErr := client.ListAgents(ctx)
		daemonByID := map[string]internal.Agent{}
		if daemonErr == nil && daemonAgents != nil {
			for _, a := range daemonAgents.Agents {
				daemonByID[a.ID] = a
			}
		}

		union := map[string]struct{}{}
		for _, id := range localIDs {
			union[id] = struct{}{}
		}
		for id := range daemonByID {
			union[id] = struct{}{}
		}

		if len(union) == 0 {
			if daemonErr != nil {
				fmt.Println("No local workers found (daemon unreachable)")
			} else {
				fmt.Println("No workers found")
			}
			return nil
		}

		ids := make([]string, 0, len(union))
		for id := range union {
			ids = append(ids, id)
		}
		sort.Strings(ids)

		fmt.Println("Workers:")
		for _, workerID := range ids {
			local := getLocalWorkerStatus(workerID)
			localLabel := "stopped"
			if local.running {
				localLabel = fmt.Sprintf("running (PID %d)", local.pid)
			} else if local.hasPIDFile {
				localLabel = fmt.Sprintf("stopped (%s)", local.reason)
			} else {
				localLabel = "stopped (no PID file)"
			}

			if daemonErr != nil {
				fmt.Printf("  - %s  local: %s  daemon: offline (daemon unreachable)\n", workerID, localLabel)
				continue
			}

			a, ok := daemonByID[workerID]
			if !ok {
				fmt.Printf("  - %s  local: %s  daemon: offline\n", workerID, localLabel)
				continue
			}

			status := a.Status
			if status == "" {
				status = "unknown"
			}
			fmt.Printf("  - %s  local: %s  daemon: %s (last_seen: %s)\n", workerID, localLabel, status, formatAgo(a.LastPong))
		}

		return nil
	},
}

func init() {
	workerUpCmd.Flags().BoolP("foreground", "f", false, "Run in foreground")
	workerUpCmd.Flags().StringP("id", "i", "", "Worker ID (auto-generated if not provided)")
	workerUpCmd.Flags().StringP("control-plane", "c", "", "Control plane URL")
	workerUpCmd.Flags().StringSlice("capabilities", []string{}, "Worker capabilities")
	workerUpCmd.Flags().IntP("max-concurrent", "m", 5, "Maximum concurrent tasks")

	workerDownCmd.Flags().BoolP("all", "a", false, "Stop all workers")
}

func isWorkerRunning(workerID string) bool {
	pidFile := getWorkerPIDFile(workerID)
	data, err := os.ReadFile(pidFile)
	if err != nil {
		return false
	}

	var pid int
	if _, err := fmt.Sscanf(string(data), "%d", &pid); err != nil || pid <= 0 {
		return false
	}

	return syscall.Kill(pid, 0) == nil
}

func getWorkerPIDFile(workerID string) string {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = findForgeRoot()
	}
	if forgeRoot == "" {
		return filepath.Join(".forge", "workers", workerID+".pid")
	}
	return filepath.Join(forgeRoot, ".forge", "workers", workerID+".pid")
}

func getWorkerLogFile(workerID string) string {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = findForgeRoot()
	}
	if forgeRoot == "" {
		return filepath.Join(".forge", "logs", "workers", workerID+".log")
	}
	return filepath.Join(forgeRoot, ".forge", "logs", "workers", workerID+".log")
}

func listRunningWorkers() ([]string, error) {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = findForgeRoot()
	}

	var workersDir string
	if forgeRoot != "" {
		workersDir = filepath.Join(forgeRoot, ".forge", "workers")
	} else {
		workersDir = ".forge/workers"
	}

	entries, err := os.ReadDir(workersDir)
	if err != nil {
		if os.IsNotExist(err) {
			return []string{}, nil
		}
		return nil, err
	}

	var workers []string
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		if filepath.Ext(entry.Name()) == ".pid" {
			workerID := entry.Name()[:len(entry.Name())-4]
			if isWorkerRunning(workerID) {
				workers = append(workers, workerID)
			} else {
				// Clean up stale PID file
				os.Remove(filepath.Join(workersDir, entry.Name()))
			}
		}
	}

	return workers, nil
}

func stopWorker(workerID string) error {
	pidFile := getWorkerPIDFile(workerID)
	data, err := os.ReadFile(pidFile)
	if err != nil {
		return fmt.Errorf("worker not running: %s", workerID)
	}

	var pid int
	if _, err := fmt.Sscanf(string(data), "%d", &pid); err != nil || pid <= 0 {
		os.Remove(pidFile)
		return fmt.Errorf("invalid PID file for worker: %s", workerID)
	}

	// Try graceful shutdown first
	if err := syscall.Kill(pid, syscall.SIGTERM); err != nil {
		// Try force kill
		syscall.Kill(pid, syscall.SIGKILL)
	}

	os.Remove(pidFile)
	return nil
}

// min returns the smaller of a and b (helper for Go < 1.21)
func min(a, b time.Duration) time.Duration {
	if a < b {
		return a
	}
	return b
}

// runWorker runs with automatic reconnection and exponential backoff.
func runWorker(ctx context.Context, workerCfg WorkerConfig) error {
	backoff := 1 * time.Second
	maxBackoff := 30 * time.Second
	attempt := 0
	maxAttempts := 10

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		if attempt > 0 {
			fmt.Printf("[worker] reconnecting in %s (attempt %d/%d)...\n", backoff, attempt, maxAttempts)
			select {
			case <-time.After(backoff):
			case <-ctx.Done():
				return ctx.Err()
			}
			backoff = min(backoff*2, maxBackoff)
		}

		err := runWorkerOnce(ctx, workerCfg)
		if err == nil || errors.Is(err, context.Canceled) {
			return err
		}

		attempt++
		if attempt >= maxAttempts {
			return fmt.Errorf("[worker] giving up after %d reconnection attempts: %w", maxAttempts, err)
		}
		fmt.Printf("[worker] connection lost: %v\n", err)
	}
}

// runWorkerOnce handles one WebSocket connection lifecycle.
// Returns nil on clean disconnect (Ctrl+C), error on network failure.
func runWorkerOnce(ctx context.Context, workerCfg WorkerConfig) error {
	wsURL := deriveWSURL(workerCfg.ControlPlane)

	fmt.Printf("Worker %s connecting to %s...\n", workerCfg.ID, wsURL)

	conn, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		return fmt.Errorf("failed to connect to v3 daemon at %s: %w\n  Recovery: ensure the v3 daemon is running (forge daemon start) and WS_PORT is set correctly", wsURL, err)
	}
	defer conn.Close()

	// Handle server pings
	conn.SetPingHandler(func(appData string) error {
		return conn.WriteMessage(websocket.PongMessage, []byte(appData))
	})

	// Determine hostname for the node field.
	hostname, _ := os.Hostname()

	caps := workerCfg.Capabilities
	if len(caps) == 0 {
		caps = []string{"cli"}
	}

	regMsg := wsEnvelope{
		Version: WebSocketProtocolVersion,
		Type:    "agent.register",
		ID:      fmt.Sprintf("reg-%d", time.Now().UnixNano()),
		Payload: wsMarshal(wsRegisterPayload{
			AgentID:      workerCfg.ID,
			Name:         workerCfg.ID,
			Node:         hostname,
			Tier:         "standard",
			Capabilities: caps,
		}),
		Time: time.Now(),
	}
	if err := conn.WriteJSON(regMsg); err != nil {
		return fmt.Errorf("failed to send registration: %w", err)
	}

	// Wait for registration acknowledgement (5 second deadline).
	conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	var ack wsEnvelope
	if err := conn.ReadJSON(&ack); err != nil {
		return fmt.Errorf("registration ack timeout or error: %w\n  Recovery: check daemon logs and ensure WebSocket endpoint is reachable at %s", err, wsURL)
	}
	conn.SetReadDeadline(time.Time{})

	if ack.Type != "agent.register_ack" {
		return fmt.Errorf("unexpected registration response type %q (expected agent.register_ack)", ack.Type)
	}

	fmt.Printf("Worker %s registered successfully\n", workerCfg.ID)
	fmt.Println("Press Ctrl+C to disconnect")

	// Inbound message channel and read goroutine.
	msgCh := make(chan wsEnvelope, 10)
	errCh := make(chan error, 1)

	go func() {
		for {
			var msg wsEnvelope
			if err := conn.ReadJSON(&msg); err != nil {
				errCh <- err
				return
			}
			msgCh <- msg
		}
	}()

	// Send a pong every 30 seconds so the daemon does not prune this worker
	// (daemon prunes workers after 60 s without a heartbeat).
	pingTicker := time.NewTicker(30 * time.Second)
	defer pingTicker.Stop()

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(sigChan)

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()

		case <-sigChan:
			fmt.Println("\nDisconnecting worker...")
			return nil

		case err := <-errCh:
			return err

		case <-pingTicker.C:
			pong := wsEnvelope{
				Version: WebSocketProtocolVersion,
				Type:    "pong",
				ID:      fmt.Sprintf("pong-%d", time.Now().Unix()),
				Time:    time.Now(),
			}
			// Best-effort; ignore write errors here — the read loop will catch disconnect.
			conn.WriteJSON(pong) //nolint:errcheck

		case msg := <-msgCh:
			handleWorkerMessage(conn, workerCfg, msg)
		}
	}
}

// handleWorkerMessage processes inbound messages from the v3 daemon.
func handleWorkerMessage(conn *websocket.Conn, cfg WorkerConfig, msg wsEnvelope) {
	switch msg.Type {
	case "ping":
		pong := wsEnvelope{
			Version: WebSocketProtocolVersion,
			Type:    "pong",
			ID:      msg.ID,
			Time:    time.Now(),
		}
		conn.WriteJSON(pong) //nolint:errcheck

	case "task.assigned":
		fmt.Printf("[worker] Task assigned: %s\n", msg.TaskID)
		// Real execution would happen here. For now, acknowledge receipt only.
		// To claim: forge task claim <id> --agent <workerID>

	case "task.pause", "task.resume", "task.cancel":
		fmt.Printf("[worker] Task control: %s for task %s\n", msg.Type, msg.TaskID)

	default:
		// Silently ignore unknown message types to stay forward-compatible.
	}
}

func startWorkerBackground(workerCfg WorkerConfig) error {
	logFile := getWorkerLogFile(workerCfg.ID)
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

	// Build command arguments
	args := []string{"worker", "up", "--foreground", "--id", workerCfg.ID}
	if workerCfg.ControlPlane != "" {
		args = append(args, "--control-plane", workerCfg.ControlPlane)
	}
	if len(workerCfg.Capabilities) > 0 {
		for _, cap := range workerCfg.Capabilities {
			args = append(args, "--capabilities", cap)
		}
	}
	if workerCfg.MaxConcurrent > 0 {
		args = append(args, "--max-concurrent", fmt.Sprintf("%d", workerCfg.MaxConcurrent))
	}

	cmd := exec.Command(exe, args...)
	cmd.Stdout = f
	cmd.Stderr = f
	cmd.SysProcAttr = &syscall.SysProcAttr{
		Setsid: true,
	}

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("failed to start worker: %w", err)
	}

	// Write PID file
	pidFile := getWorkerPIDFile(workerCfg.ID)
	if err := os.MkdirAll(filepath.Dir(pidFile), 0755); err != nil {
		cmd.Process.Kill()
		return fmt.Errorf("failed to create workers directory: %w", err)
	}

	if err := os.WriteFile(pidFile, []byte(fmt.Sprintf("%d", cmd.Process.Pid)), 0644); err != nil {
		cmd.Process.Kill()
		return fmt.Errorf("failed to write PID file: %w", err)
	}

	fmt.Printf("Worker %s started in background (PID: %d)\n", workerCfg.ID, cmd.Process.Pid)
	fmt.Printf("Logs: %s\n", logFile)

	// Don't wait - detach
	go func() {
		cmd.Wait()
		os.Remove(pidFile)
	}()

	time.Sleep(500 * time.Millisecond)
	return nil
}
