package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// nodeCmd represents the node noun
var nodeCmd = &cobra.Command{
	Use:   "node",
	Short: "Manage XNode mesh nodes",
	Long:  "List and inspect nodes in the FORGE XNode mesh (Tailscale peers).",
}

// XNodeInfo is the API response shape from GET /api/xnode/nodes
type XNodeInfo struct {
	ID            string    `json:"id"`
	Hostname      string    `json:"hostname"`
	Address       string    `json:"address"`
	Status        string    `json:"status"`
	LastHeartbeat time.Time `json:"last_heartbeat"`
}

// nodeListCmd: forge node list
var nodeListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all nodes in the XNode mesh",
	Long:  "Show all registered nodes and their status from the XNode mesh.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		client := internal.NewClient()

		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		resp, err := client.Get(ctx, "/xnode/nodes")
		if err != nil {
			return err
		}
		defer resp.Body.Close()

		if resp.StatusCode == http.StatusNotFound {
			return fmt.Errorf("daemon not running or XNode endpoint unavailable — start with: forge daemon start")
		}

		if err := internal.CheckResponse(resp); err != nil {
			return err
		}

		var nodes []XNodeInfo
		if err := json.NewDecoder(resp.Body).Decode(&nodes); err != nil {
			return fmt.Errorf("decode nodes: %w", err)
		}

		if format == "json" {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(nodes)
		}

		if len(nodes) == 0 {
			fmt.Println("No nodes registered in XNode mesh.")
			fmt.Println("  Run `forge node register` or check daemon startup scripts.")
			return nil
		}

		w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
		fmt.Fprintln(w, "NODE\tHOSTNAME\tADDRESS\tSTATUS\tLAST HEARTBEAT")
		now := time.Now()
		for _, n := range nodes {
			age := ""
			if !n.LastHeartbeat.IsZero() {
				d := now.Sub(n.LastHeartbeat).Truncate(time.Second)
				age = d.String() + " ago"
			}
			fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\n",
				n.ID, n.Hostname, n.Address, statusSymbol(n.Status), age)
		}
		return w.Flush()
	},
}

// nodeStatusCmd: forge node status [node-id]
var nodeStatusCmd = &cobra.Command{
	Use:   "status [node-id]",
	Short: "Show mesh status or ping a specific node",
	Long:  "Without a node-id, shows the overall XNode mesh status. With a node-id, pings that node's health endpoint.",
	Args:  cobra.MaximumNArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		if len(args) == 0 {
			// Show overall mesh status from local daemon
			return showMeshStatus(format)
		}

		// Ping a specific node by ID
		nodeID := args[0]
		return pingNode(nodeID, format)
	},
}

// showMeshStatus queries GET /api/xnode/status
func showMeshStatus(format string) error {
	client := internal.NewClient()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/xnode/status")
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if err := internal.CheckResponse(resp); err != nil {
		return err
	}

	var status map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&status); err != nil {
		return fmt.Errorf("decode status: %w", err)
	}

	if format == "json" {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(status)
	}

	fmt.Println("XNode Mesh Status:")
	for k, v := range status {
		fmt.Printf("  %s: %v\n", k, v)
	}
	return nil
}

// pingNode tests connectivity to a named node
func pingNode(nodeID, format string) error {
	// Node addresses follow Tailscale Magic DNS: http://<node-id>:8081
	nodeURL := fmt.Sprintf("http://%s:8081", nodeID)

	fmt.Printf("Pinging %s (%s)... ", nodeID, nodeURL)
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get(nodeURL + "/health")
	if err != nil {
		fmt.Println("UNREACHABLE")
		if format != "quiet" {
			fmt.Printf("  Error: %v\n", err)
			fmt.Printf("  Check: tailscale status | grep %s\n", nodeID)
		}
		return fmt.Errorf("node %s unreachable", nodeID)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusOK {
		fmt.Println("OK")
	} else {
		fmt.Printf("HTTP %d\n", resp.StatusCode)
	}

	if format == "json" {
		var health map[string]interface{}
		json.NewDecoder(resp.Body).Decode(&health) //nolint:errcheck
		health["node"] = nodeID
		health["url"] = nodeURL
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(health)
	}

	return nil
}

// printKnownNodes is called when the XNode endpoint is unavailable.
// The static node list has been removed — node discovery requires a running daemon.
func printKnownNodes(format string) {
	msg := "XNode endpoint unavailable — start the daemon with: forge daemon start"
	if format == "json" {
		fmt.Printf(`{"error": %q}` + "\n", msg)
		return
	}
	fmt.Println(msg)
}

// statusSymbol returns a display symbol for a node status
func statusSymbol(status string) string {
	switch status {
	case "online", "active":
		return "online"
	case "offline", "disconnected":
		return "OFFLINE"
	case "degraded", "warning":
		return "degraded"
	default:
		return status
	}
}

// defaultAgentsForNode returns the conventional default agent list for a node ID.
func defaultAgentsForNode(nodeID string) []string {
	switch nodeID {
	case "sati":
		return []string{"kimi", "kimi-2"}
	case "nova":
		return []string{"glm", "gemini"}
	case "gaea":
		return []string{"pi", "minimax"}
	case "vega":
		return []string{"pi"}
	case "prya":
		return []string{} // lead only — no fleet agents on prya
	default:
		return []string{}
	}
}

// detectNodeAddr attempts to auto-detect the node's public address.
// It tries `tailscale ip -4` first, then falls back to hostname resolution.
func detectNodeAddr() string {
	tsCmd := exec.Command("tailscale", "ip", "-4")
	if out, err := tsCmd.Output(); err == nil {
		ip := strings.TrimSpace(string(out))
		if ip != "" {
			return ip + ":8081"
		}
	}
	// Fallback: use os.Hostname as the address (Tailscale Magic DNS)
	h, _ := os.Hostname()
	return h + ":8081"
}

// nodeJoinCmd: forge node join
var nodeJoinCmd = &cobra.Command{
	Use:   "join",
	Short: "Register this node with the FORGE hub and start fleet agents",
	Long: `Register this node with the FORGE hub (forged daemon) and optionally start
agent work loops. Convention over configuration: node ID, address, hub URL, and
default agents are all auto-detected from the environment.

Examples:
  forge node join                              # auto-detect everything
  forge node join --agents "kimi minimax"      # override agents
  forge node join --node-id node-a --hub http://forge-control-plane:8081
  forge node join --check                      # status check only, no changes`,
	RunE: func(cmd *cobra.Command, args []string) error {
		nodeID, _ := cmd.Flags().GetString("node-id")
		nodeAddr, _ := cmd.Flags().GetString("node-addr")
		hub, _ := cmd.Flags().GetString("hub")
		agentsFlag, _ := cmd.Flags().GetString("agents")
		checkOnly, _ := cmd.Flags().GetBool("check")
		noPull, _ := cmd.Flags().GetBool("no-pull")
		noBuild, _ := cmd.Flags().GetBool("no-build")

		// Apply defaults
		if nodeID == "" {
			h, err := os.Hostname()
			if err != nil {
				return fmt.Errorf("[join] ERROR: cannot determine hostname: %w\n  Fix: pass --node-id explicitly", err)
			}
			// Use short hostname (strip domain suffix like `hostname -s`)
			nodeID = strings.SplitN(h, ".", 2)[0]
		}

		if nodeAddr == "" {
			nodeAddr = detectNodeAddr()
		} else {
			// Ensure port suffix
			if !strings.Contains(nodeAddr, ":") {
				nodeAddr = nodeAddr + ":8081"
			}
		}

		if hub == "" {
			hub = os.Getenv("FORGE_API_URL")
			if hub == "" {
				hub = "http://localhost:8081"
			}
		}

		// Determine agent list
		var agents []string
		if agentsFlag != "" {
			agents = strings.Fields(agentsFlag)
		} else {
			agents = defaultAgentsForNode(nodeID)
		}

		agentsDisplay := strings.Join(agents, " ")
		if agentsDisplay == "" {
			agentsDisplay = "none"
		}

		fmt.Printf("[join] Node:    %s\n", nodeID)
		fmt.Printf("[join] Address: %s\n", nodeAddr)
		fmt.Printf("[join] Hub:     %s\n", hub)
		fmt.Printf("[join] Agents:  %s\n", agentsDisplay)

		// --- Check-only path ---
		if checkOnly {
			fmt.Println()
			fmt.Println("=== Hub reachability ===")
			hc := &http.Client{Timeout: 5 * time.Second}
			resp, err := hc.Get(hub + "/health")
			if err != nil {
				fmt.Printf("  %s UNREACHABLE\n", hub)
				fmt.Printf("  Error: %v\n", err)
				fmt.Printf("  Check: tailscale status | grep prya\n")
			} else {
				resp.Body.Close()
				fmt.Printf("  %s OK (HTTP %d)\n", hub, resp.StatusCode)
			}
			fmt.Println()
			fmt.Println("=== Node list ===")
			// Re-use the existing nodeListCmd logic via the client pointed at hub
			client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(hub))
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			listResp, listErr := client.Get(ctx, "/xnode/nodes")
			if listErr != nil {
				fmt.Printf("  (node list unavailable: %v)\n", listErr)
				return nil
			}
			defer listResp.Body.Close()
			if listResp.StatusCode == http.StatusNotFound {
				printKnownNodes("")
				return nil
			}
			if err := internal.CheckResponse(listResp); err != nil {
				fmt.Printf("  (node list error: %v)\n", err)
				return nil
			}
			var nodes []XNodeInfo
			if err := json.NewDecoder(listResp.Body).Decode(&nodes); err != nil {
				fmt.Printf("  (decode error: %v)\n", err)
				return nil
			}
			w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
			fmt.Fprintln(w, "NODE\tHOSTNAME\tADDRESS\tSTATUS\tLAST HEARTBEAT")
			now := time.Now()
			for _, n := range nodes {
				age := ""
				if !n.LastHeartbeat.IsZero() {
					d := now.Sub(n.LastHeartbeat).Truncate(time.Second)
					age = d.String() + " ago"
				}
				fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\n",
					n.ID, n.Hostname, n.Address, statusSymbol(n.Status), age)
			}
			return w.Flush()
		}

		// --- Step 1: git pull ---
		if !noPull {
			fmt.Println()
			fmt.Println("[join] Pulling latest code...")
			pullCmd := exec.Command("git", "pull", "--rebase", "--quiet")
			pullCmd.Stdout = os.Stdout
			pullCmd.Stderr = os.Stderr
			if err := pullCmd.Run(); err != nil {
				fmt.Printf("[join] WARN: git pull failed (%v), continuing with current code\n", err)
			}
		}

		// --- Step 2: ensure forge CLI is built ---
		if !noBuild {
			// Check if the binary already exists alongside this running process.
			// We look for cmd/forge/forge relative to the executable's directory.
			exePath, _ := os.Executable()
			if exePath == "" {
				exePath = "forge"
			}
			if _, err := os.Stat(exePath); err != nil {
				fmt.Println("[join] Building forge CLI...")
				buildCmd := exec.Command("go", "build", "-o", "forge", ".")
				buildCmd.Dir = "cmd/forge"
				buildCmd.Stdout = os.Stdout
				buildCmd.Stderr = os.Stderr
				if buildErr := buildCmd.Run(); buildErr != nil {
					return fmt.Errorf("[join] ERROR: forge CLI build failed: %w\n  Fix: cd cmd/forge && go build -o forge .", buildErr)
				}
			}
		}

		// --- Step 3: verify hub reachable ---
		fmt.Printf("[join] Checking hub at %s...\n", hub)
		hc := &http.Client{Timeout: 5 * time.Second}
		healthResp, err := hc.Get(hub + "/health")
		if err != nil {
			return fmt.Errorf("[join] ERROR: hub unreachable at %s\n  Check: tailscale status | grep prya\n  Error: %w", hub, err)
		}
		healthResp.Body.Close()
		if healthResp.StatusCode < 200 || healthResp.StatusCode >= 300 {
			return fmt.Errorf("[join] ERROR: hub returned HTTP %d at %s/health\n  Check: forge daemon status", healthResp.StatusCode, hub)
		}
		fmt.Println("[join] Hub reachable.")

		// --- Step 4: register this node ---
		fmt.Printf("[join] Registering node %s at %s...\n", nodeID, nodeAddr)
		client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(hub))
		// Apply FORGE_TOKEN bearer auth if set
		if token := os.Getenv("FORGE_TOKEN"); token != "" {
			client = internal.NewClientWithURL(internal.NormalizeAPIBaseURL(hub), internal.WithToken(token))
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

		regCtx, regCancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer regCancel()
		regResp, regErr := client.Post(regCtx, "/xnode/nodes/register", payload)
		if regErr != nil {
			return fmt.Errorf("[join] ERROR: registration failed: %w\n  Check: forge daemon status && curl %s/health", regErr, hub)
		}
		defer regResp.Body.Close()
		if err := internal.CheckResponse(regResp); err != nil {
			return fmt.Errorf("[join] ERROR: registration rejected: %w\n  Hub: %s\n  Node ID: %s", err, hub, nodeID)
		}
		fmt.Printf("[join] Node %s registered.\n", nodeID)

		// --- Step 5: verify in node list ---
		fmt.Println()
		fmt.Println("=== Node list ===")
		listCtx, listCancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer listCancel()
		listResp, listErr := client.Get(listCtx, "/xnode/nodes")
		if listErr != nil {
			fmt.Printf("  (node list unavailable: %v)\n", listErr)
		} else {
			defer listResp.Body.Close()
			if listResp.StatusCode == http.StatusNotFound {
				printKnownNodes("")
			} else if err := internal.CheckResponse(listResp); err != nil {
				fmt.Printf("  (node list error: %v)\n", err)
			} else {
				var nodes []XNodeInfo
				if err := json.NewDecoder(listResp.Body).Decode(&nodes); err == nil {
					w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
					fmt.Fprintln(w, "NODE\tHOSTNAME\tADDRESS\tSTATUS\tLAST HEARTBEAT")
					now := time.Now()
					for _, n := range nodes {
						age := ""
						if !n.LastHeartbeat.IsZero() {
							d := now.Sub(n.LastHeartbeat).Truncate(time.Second)
							age = d.String() + " ago"
						}
						fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\n",
							n.ID, n.Hostname, n.Address, statusSymbol(n.Status), age)
					}
					w.Flush() //nolint:errcheck
				}
			}
		}
		fmt.Println()

		// --- Step 6: start agent work loops ---
		if len(agents) == 0 {
			fmt.Printf("[join] No agents configured for %s — done.\n", nodeID)
			fmt.Printf("       Set --agents \"pi minimax\" to start agent work loops.\n")
			fmt.Printf("       Verify: FORGE_API_URL=%s forge node list\n", hub)
			return nil
		}

		// Find forge binary: prefer executable path, then PATH
		forgeBin, _ := os.Executable()
		if forgeBin == "" {
			forgeBin = "forge"
		}

		fmt.Printf("[join] Starting agent work loops: %s\n", strings.Join(agents, " "))
		for _, agent := range agents {
			fmt.Printf("[join]   -> %s (forge work --daemon)\n", agent)
			workCmd := exec.Command(forgeBin, "work", "--daemon", "--agent", agent, "--interval", "30s")
			workCmd.Env = append(os.Environ(),
				"NODE_ID="+nodeID,
				"FORGE_AGENT_TYPE=fleet",
				"FORGE_AGENT_NAME="+agent,
				"FORGE_API_URL="+hub,
			)
			// Detach: do not wait, do not inherit stdin/stdout/stderr
			if startErr := workCmd.Start(); startErr != nil {
				fmt.Printf("[join]   WARN: failed to start agent %s: %v\n", agent, startErr)
				continue
			}
			// Release the process so it runs independently
			if releaseErr := workCmd.Process.Release(); releaseErr != nil {
				fmt.Printf("[join]   WARN: failed to release agent %s process: %v\n", agent, releaseErr)
			}
		}

		fmt.Println()
		fmt.Printf("[join] Done. Node %s is connected.\n", nodeID)
		fmt.Printf("       Verify: FORGE_API_URL=%s forge node list\n", hub)
		fmt.Printf("       Agents: FORGE_API_URL=%s forge agent list\n", hub)
		return nil
	},
}

func init() {
	nodeCmd.AddCommand(nodeListCmd)
	nodeCmd.AddCommand(nodeStatusCmd)
	nodeCmd.AddCommand(nodeJoinCmd)

	// nodeJoinCmd flags
	nodeJoinCmd.Flags().String("node-id", "", "Node ID (default: short hostname)")
	nodeJoinCmd.Flags().String("node-addr", "", "Node address with port (default: tailscale ip -4, fallback to hostname:8081)")
	nodeJoinCmd.Flags().String("hub", "", "Hub URL (default: $FORGE_API_URL or http://localhost:8081)")
	nodeJoinCmd.Flags().String("agents", "", "Space-separated agent names to start as work loops (default: convention table)")
	nodeJoinCmd.Flags().Bool("check", false, "Status check only — verify hub reachability and show node list, no changes")
	nodeJoinCmd.Flags().Bool("no-pull", false, "Skip git pull step")
	nodeJoinCmd.Flags().Bool("no-build", false, "Skip forge CLI rebuild check")
}
