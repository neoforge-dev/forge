package main

import (
	"bufio"
	"context"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

// nodeConfig is the optional per-node YAML schema in config/nodes/<hostname>.yaml.
type nodeConfig struct {
	Agents []string `yaml:"agents"`
}

// resolveShortHostname returns the short hostname (strips domain suffix).
func resolveShortHostname() (string, error) {
	h, err := os.Hostname()
	if err != nil {
		return "", fmt.Errorf("cannot determine hostname: %w — pass --node-id explicitly", err)
	}
	return strings.SplitN(h, ".", 2)[0], nil
}

// loadNodeConfig reads config/nodes/<nodeID>.yaml relative to the repo root
// (FORGE_ROOT env → cwd fallback). Returns nil if the file is absent.
func loadNodeConfig(nodeID string) (*nodeConfig, error) {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		var err error
		root, err = os.Getwd()
		if err != nil {
			return nil, nil //nolint:nilerr // best-effort; no config is fine
		}
	}
	cfgPath := filepath.Join(root, "config", "nodes", nodeID+".yaml")
	data, err := os.ReadFile(cfgPath)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", cfgPath, err)
	}
	var nc nodeConfig
	if err := yaml.Unmarshal(data, &nc); err != nil {
		return nil, fmt.Errorf("parse %s: %w", cfgPath, err)
	}
	return &nc, nil
}

// isHubNode returns true when this node is the control-plane hub (i.e. the
// FORGE_API_URL points at localhost or at the current hostname).
func isHubNode(nodeID string) bool {
	apiURL := internal.ResolveControlPlaneURL()
	apiURL = strings.ToLower(apiURL)
	if strings.Contains(apiURL, "localhost") || strings.Contains(apiURL, "127.0.0.1") {
		return true
	}
	// Also treat as hub if the URL hostname matches our own short hostname.
	return strings.Contains(apiURL, strings.ToLower(nodeID))
}

// waitForDaemonHealth polls the health endpoint until it responds or the
// deadline expires. Returns nil on success.
func waitForDaemonHealth(healthURL string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	client := &http.Client{Timeout: 2 * time.Second}
	for time.Now().Before(deadline) {
		resp, err := client.Get(healthURL)
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				return nil
			}
		}
		time.Sleep(500 * time.Millisecond)
	}
	return fmt.Errorf("daemon did not become healthy at %s within %s — check: forge daemon status", healthURL, timeout)
}

// daemonLogPath returns the canonical daemon log path (same logic as daemon status).
func daemonLogPath() string {
	if home, err := os.UserHomeDir(); err == nil {
		p := filepath.Join(home, ".forge", "logs", "v3-daemon.log")
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return "/tmp/forged.log"
}

// watchdogScriptPath returns the path to daemon-watchdog.sh if it exists.
func watchdogScriptPath() string {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		if cwd, err := os.Getwd(); err == nil {
			root = cwd
		}
	}
	if root == "" {
		return ""
	}
	p := filepath.Join(root, "bin", "daemon-watchdog.sh")
	if _, err := os.Stat(p); err == nil {
		return p
	}
	return ""
}

// registerNode sends a POST /xnode/nodes/register to the hub.
func registerNode(nodeID, nodeAddr, hub string, dryRun bool) error {
	if dryRun {
		fmt.Printf("[up] (dry-run) would register node %s at %s with hub %s\n", nodeID, nodeAddr, hub)
		return nil
	}
	type registerPayload struct {
		ID       string `json:"id"`
		Hostname string `json:"hostname"`
		Address  string `json:"address"`
		Status   string `json:"status"`
	}
	payload := &registerPayload{
		ID:       nodeID,
		Hostname: nodeID,
		Address:  nodeAddr,
		Status:   "online",
	}

	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(hub))
	if token := os.Getenv("FORGE_TOKEN"); token != "" {
		client = internal.NewClientWithURL(internal.NormalizeAPIBaseURL(hub), internal.WithToken(token))
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	resp, err := client.Post(ctx, "/xnode/nodes/register", payload)
	if err != nil {
		// Non-fatal: node might just be unreachable temporarily.
		fmt.Printf("[up] WARN: registration failed: %v\n", err)
		return nil
	}
	defer resp.Body.Close()
	if err := internal.CheckResponse(resp); err != nil {
		fmt.Printf("[up] WARN: registration rejected: %v\n", err)
		return nil
	}
	fmt.Printf("[up] Node %s registered with hub.\n", nodeID)
	return nil
}

// resolveAgents determines the agent list with the priority chain described
// in the design: flag > node config file > convention map > empty.
func resolveAgents(nodeID, flagValue string) ([]string, string, error) {
	if flagValue != "" {
		agents := strings.FieldsFunc(flagValue, func(r rune) bool {
			return r == ',' || r == ' '
		})
		return agents, "flag", nil
	}

	nc, err := loadNodeConfig(nodeID)
	if err != nil {
		return nil, "", err
	}
	if nc != nil {
		return nc.Agents, "config/nodes/" + nodeID + ".yaml", nil
	}

	agents := defaultAgentsForNode(nodeID)
	return agents, "convention table", nil
}

// ─────────────────────────────────────────────────────────────────────────────
// forge node up
// ─────────────────────────────────────────────────────────────────────────────

var nodeUpCmd = &cobra.Command{
	Use:   "up",
	Short: "Bring this node online: pull, build, daemon, tmux sessions",
	Long: `Idempotent node startup. Replaces .forge/scripts/forge-startup.sh.

Steps (each step is skipped if already satisfied):
  1. git pull (--no-pull to skip)
  2. rebuild forge CLI if source is newer (--skip-build to skip)
  3. start daemon if hub node and not already running (--skip-daemon to skip)
  4. wait for daemon health
  5. create/ensure tmux 'forge' session with one window per agent
  6. create/ensure tmux 'forge-monitor' session with standard windows
  7. register this node with the hub

Examples:
  forge node up                             # auto-detect everything
  forge node up --dry-run                   # preview actions, no changes
  forge node up --agents kimi,pi --no-pull  # custom agents, skip git pull
  forge node up --skip-daemon               # bring up tmux only`,
	RunE: func(cmd *cobra.Command, args []string) error {
		if _, err := exec.LookPath("tmux"); err != nil {
			return fmt.Errorf("tmux not found in PATH.\n\n  Install: apt install tmux (Linux) or brew install tmux (macOS)")
		}

		skipBuild, _ := cmd.Flags().GetBool("skip-build")
		skipDaemon, _ := cmd.Flags().GetBool("skip-daemon")
		agentsFlag, _ := cmd.Flags().GetString("agents")
		noPull, _ := cmd.Flags().GetBool("no-pull")
		dryRun, _ := cmd.Flags().GetBool("dry-run")

		// ── 0. Identify node ────────────────────────────────────────────────
		nodeID, err := resolveShortHostname()
		if err != nil {
			return err
		}
		hub := internal.ResolveControlPlaneURL()
		hubNode := isHubNode(nodeID)

		fmt.Printf("[up] Node:     %s\n", nodeID)
		fmt.Printf("[up] Hub:      %s\n", hub)
		fmt.Printf("[up] Is hub:   %v\n", hubNode)
		if dryRun {
			fmt.Println("[up] Mode:     dry-run (no changes will be made)")
		}

		// ── 1. Git pull ──────────────────────────────────────────────────────
		if !noPull {
			fmt.Println("[up] Step 1/7: git pull...")
			if dryRun {
				fmt.Println("[up] (dry-run) would run: git pull --no-rebase --quiet")
			} else {
				pullCmd := exec.Command("git", "pull", "--no-rebase", "--quiet")
				pullCmd.Stdout = os.Stdout
				pullCmd.Stderr = os.Stderr
				if pullErr := pullCmd.Run(); pullErr != nil {
					fmt.Printf("[up] WARN: git pull failed (%v), continuing with current code\n", pullErr)
				}
			}
		} else {
			fmt.Println("[up] Step 1/7: git pull — skipped (--no-pull)")
		}

		// ── 2. Build CLI ─────────────────────────────────────────────────────
		if !skipBuild {
			fmt.Println("[up] Step 2/7: ensure forge CLI is built...")
			exePath, _ := os.Executable()
			needsBuild := true
			if exePath != "" {
				exeStat, exeErr := os.Stat(exePath)
				if exeErr == nil {
					// Check if any .go source file is newer than the binary.
					root := os.Getenv("FORGE_ROOT")
					if root == "" {
						if cwd, err := os.Getwd(); err == nil {
							root = cwd
						}
					}
					if root != "" {
						srcDir := filepath.Join(root, "cmd", "forge")
						entries, rdErr := os.ReadDir(srcDir)
						if rdErr == nil {
							needsBuild = false
							for _, e := range entries {
								if e.IsDir() || !strings.HasSuffix(e.Name(), ".go") {
									continue
								}
								info, stErr := e.Info()
								if stErr == nil && info.ModTime().After(exeStat.ModTime()) {
									needsBuild = true
									break
								}
							}
						}
					}
				}
			}

			if needsBuild {
				root := os.Getenv("FORGE_ROOT")
				if root == "" {
					if cwd, err := os.Getwd(); err == nil {
						root = cwd
					}
				}
				srcDir := filepath.Join(root, "cmd", "forge")
				if dryRun {
					fmt.Printf("[up] (dry-run) would run: go build -o forge . in %s\n", srcDir)
				} else {
					fmt.Printf("[up] Building forge CLI in %s...\n", srcDir)
					buildCmd := exec.Command("go", "build", "-o", "forge", ".")
					buildCmd.Dir = srcDir
					buildCmd.Stdout = os.Stdout
					buildCmd.Stderr = os.Stderr
					if buildErr := buildCmd.Run(); buildErr != nil {
						return fmt.Errorf("[up] Build failed: %w\n  Recovery: cd %s && go build -o forge .", buildErr, srcDir)
					}
					fmt.Println("[up] Build OK.")
				}
			} else {
				fmt.Println("[up] forge CLI binary is up to date — skipping build.")
			}
		} else {
			fmt.Println("[up] Step 2/7: build — skipped (--skip-build)")
		}

		// ── 3. Start daemon (hub-only) ───────────────────────────────────────
		apiPort := internal.ResolveAPIPort()
		if !skipDaemon && hubNode {
			fmt.Println("[up] Step 3/7: ensure daemon is running...")
			if !dryRun && isPortListening(apiPort) {
				fmt.Printf("[up] Daemon already listening on :%s — skipped.\n", apiPort)
			} else {
				if dryRun {
					fmt.Println("[up] (dry-run) would run: forge daemon start")
				} else {
					fmt.Println("[up] Starting daemon...")
					startOut, startErr := exec.Command("forge", "daemon", "start").CombinedOutput()
					if startErr != nil {
						return fmt.Errorf("[up] daemon start failed: %w\n  Output: %s\n  Recovery: forge daemon start", startErr, strings.TrimSpace(string(startOut)))
					}
				}
			}
		} else if !hubNode {
			fmt.Println("[up] Step 3/7: daemon — skipped (not hub node)")
		} else {
			fmt.Println("[up] Step 3/7: daemon — skipped (--skip-daemon)")
		}

		// ── 4. Wait for daemon health ────────────────────────────────────────
		fmt.Println("[up] Step 4/7: waiting for daemon health...")
		healthURL := hub + "/health"
		if dryRun {
			fmt.Printf("[up] (dry-run) would poll %s for up to 10s\n", healthURL)
		} else {
			if err := waitForDaemonHealth(healthURL, 10*time.Second); err != nil {
				fmt.Printf("[up] WARN: %v\n", err)
				fmt.Println("[up] Continuing — daemon may be on another node.")
			} else {
				fmt.Printf("[up] Daemon healthy at %s\n", healthURL)
			}
		}

		// ── 5. Determine agents ──────────────────────────────────────────────
		agents, agentSource, err := resolveAgents(nodeID, agentsFlag)
		if err != nil {
			return fmt.Errorf("[up] agent resolution: %w", err)
		}
		agentDisplay := strings.Join(agents, ", ")
		if agentDisplay == "" {
			agentDisplay = "none (lead only)"
		}
		fmt.Printf("[up] Agents:   %s (from %s)\n", agentDisplay, agentSource)

		// ── 6. tmux 'forge' session ──────────────────────────────────────────
		fmt.Println("[up] Step 5/7: ensure tmux 'forge' session...")
		if dryRun {
			fmt.Printf("[up] (dry-run) would ensure tmux session 'forge' with windows: %s + %v\n", nodeID, agents)
		} else {
			if err := ensureTmuxSession("forge"); err != nil {
				return fmt.Errorf("[up] %w", err)
			}
			// Rename the first window to the hostname (idempotent via rename-window).
			exec.Command("tmux", "rename-window", "-t", "forge:0", nodeID).Run() //nolint:errcheck

			for _, agent := range agents {
				if err := ensureTmuxWindow("forge", agent); err != nil {
					fmt.Printf("[up] WARN: could not create window for agent %s: %v\n", agent, err)
					continue
				}
				fmt.Printf("[up]   forge:%s — ready\n", agent)
			}
		}

		// ── 7. tmux 'forge-monitor' session ─────────────────────────────────
		fmt.Println("[up] Step 6/7: ensure tmux 'forge-monitor' session...")

		type monitorWindow struct {
			name    string
			command string
			skip    bool
		}

		logPath := daemonLogPath()
		watchdog := watchdogScriptPath()

		monitorWindows := []monitorWindow{
			{name: "daemon", command: "tail -f " + logPath},
			{name: "status", command: "watch -n 5 'forge status 2>&1 | head -40'"},
			{name: "tasks", command: "watch -n 10 'forge task list 2>&1 | head -30'"},
			{name: "patrols", command: "watch -n 30 'forge patrol list 2>&1 | head -30'"},
			{name: "dispatcher", command: "forge work --daemon --execute --interval 15s"},
			{name: "watchdog", command: watchdog, skip: watchdog == ""},
		}

		if dryRun {
			fmt.Println("[up] (dry-run) would ensure tmux session 'forge-monitor' with windows:")
			for _, w := range monitorWindows {
				if w.skip {
					fmt.Printf("[up]   %-12s — skipped (script not found)\n", w.name)
					continue
				}
				fmt.Printf("[up]   %-12s: %s\n", w.name, w.command)
			}
		} else {
			if err := ensureTmuxSession("forge-monitor"); err != nil {
				return fmt.Errorf("[up] %w", err)
			}
			for _, w := range monitorWindows {
				if w.skip {
					fmt.Printf("[up]   forge-monitor:%s — skipped (watchdog not found)\n", w.name)
					continue
				}
				if err := ensureTmuxWindow("forge-monitor", w.name); err != nil {
					fmt.Printf("[up] WARN: could not create window %s: %v\n", w.name, err)
					continue
				}
				// Only send the command if the window was freshly created (not already
				// running). We check by querying pane content — simplest heuristic is
				// to always send; tmux will ignore if already running for long-lived
				// processes (they won't crash on duplicate input).
				if err := sendToTmuxWindow("forge-monitor", w.name, w.command); err != nil {
					fmt.Printf("[up] WARN: could not send command to forge-monitor:%s: %v\n", w.name, err)
				} else {
					fmt.Printf("[up]   forge-monitor:%s: %s\n", w.name, w.command)
				}
			}
		}

		// ── 8. Register node ─────────────────────────────────────────────────
		fmt.Println("[up] Step 7/7: register node with hub...")
		nodeAddr := detectNodeAddr()
		if err := registerNode(nodeID, nodeAddr, hub, dryRun); err != nil {
			return err
		}

		// ── Summary ──────────────────────────────────────────────────────────
		fmt.Println()
		fmt.Printf("[up] Node %s is online.\n", nodeID)
		fmt.Printf("     tmux sessions: forge (lead + agents), forge-monitor (observability)\n")
		fmt.Printf("     Verify: forge node list\n")
		fmt.Printf("     Agents: forge agent list\n")
		return nil
	},
}

// ─────────────────────────────────────────────────────────────────────────────
// forge node down
// ─────────────────────────────────────────────────────────────────────────────

var nodeDownCmd = &cobra.Command{
	Use:   "down",
	Short: "Bring this node offline: kill agent windows and forge-monitor session",
	Long: `Idempotent node shutdown. Inverse of 'forge node up'.

Steps:
  1. Kill tmux 'forge-monitor' session
  2. Kill agent windows in 'forge' session (keeps the lead/hostname window)
  3. Stop daemon unless --keep-daemon

Examples:
  forge node down               # full shutdown
  forge node down --keep-daemon # shut down agents but leave daemon running
  forge node down --force       # kill everything including the lead window`,
	RunE: func(cmd *cobra.Command, args []string) error {
		keepDaemon, _ := cmd.Flags().GetBool("keep-daemon")
		force, _ := cmd.Flags().GetBool("force")

		nodeID, err := resolveShortHostname()
		if err != nil {
			return err
		}

		fmt.Printf("[down] Node: %s\n", nodeID)

		// ── 1. Kill forge-monitor ─────────────────────────────────────────────
		fmt.Println("[down] Step 1/3: kill tmux 'forge-monitor' session...")
		if err := killTmuxSession("forge-monitor"); err != nil {
			fmt.Printf("[down] WARN: %v\n", err)
		} else {
			fmt.Println("[down] forge-monitor killed (or was not running).")
		}

		// ── 2. Kill agent windows in 'forge' session ──────────────────────────
		fmt.Println("[down] Step 2/3: kill agent windows in 'forge' session...")
		if !tmuxHasSession("forge") {
			fmt.Println("[down] 'forge' session does not exist — skipped.")
		} else {
			out, err := exec.Command("tmux", "list-windows", "-t", "forge", "-F", "#{window_name}").Output()
			if err != nil {
				fmt.Printf("[down] WARN: could not list forge windows: %v\n", err)
			} else {
				scanner := bufio.NewScanner(strings.NewReader(strings.TrimSpace(string(out))))
				for scanner.Scan() {
					windowName := strings.TrimSpace(scanner.Text())
					if windowName == "" {
						continue
					}
					// Keep the lead (hostname) window unless --force
					if !force && windowName == nodeID {
						fmt.Printf("[down]   forge:%s — kept (lead window)\n", windowName)
						continue
					}
					killOut, killErr := exec.Command("tmux", "kill-window", "-t", "forge:"+windowName).CombinedOutput()
					if killErr != nil {
						fmt.Printf("[down] WARN: could not kill forge:%s: %v — %s\n", windowName, killErr, strings.TrimSpace(string(killOut)))
					} else {
						fmt.Printf("[down]   forge:%s — killed\n", windowName)
					}
				}
			}
		}

		// ── 3. Stop daemon ────────────────────────────────────────────────────
		if !keepDaemon {
			fmt.Println("[down] Step 3/3: stop daemon...")
			apiPort := internal.ResolveAPIPort()
			if !isPortListening(apiPort) {
				fmt.Printf("[down] Daemon not listening on :%s — skipped.\n", apiPort)
			} else {
				stopOut, stopErr := exec.Command("forge", "daemon", "stop").CombinedOutput()
				if stopErr != nil {
					fmt.Printf("[down] WARN: daemon stop failed: %v — %s\n", stopErr, strings.TrimSpace(string(stopOut)))
				} else {
					fmt.Println("[down] Daemon stopped.")
				}
			}
		} else {
			fmt.Println("[down] Step 3/3: daemon — skipped (--keep-daemon)")
		}

		fmt.Println()
		fmt.Printf("[down] Node %s is offline.\n", nodeID)
		return nil
	},
}

func init() {
	// Register sub-commands
	nodeCmd.AddCommand(nodeUpCmd)
	nodeCmd.AddCommand(nodeDownCmd)

	// nodeUpCmd flags
	nodeUpCmd.Flags().Bool("skip-build", false, "Skip forge CLI rebuild check")
	nodeUpCmd.Flags().Bool("skip-daemon", false, "Skip daemon start (useful on worker nodes)")
	nodeUpCmd.Flags().String("agents", "", "Comma or space-separated agent names (overrides config/nodes/<hostname>.yaml and convention table)")
	nodeUpCmd.Flags().Bool("no-pull", false, "Skip git pull step")
	nodeUpCmd.Flags().Bool("dry-run", false, "Preview actions without making any changes")

	// nodeDownCmd flags
	nodeDownCmd.Flags().Bool("keep-daemon", false, "Do not stop the daemon")
	nodeDownCmd.Flags().Bool("force", false, "Also kill the lead (hostname) tmux window")
}
