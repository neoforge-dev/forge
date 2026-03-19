package main

import (
	"bufio"
	"context"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
	"github.com/spf13/viper"
)

// configCmd represents the config noun
var configCmd = &cobra.Command{
	Use:   "config",
	Short: "Manage configuration",
	Long:  "Get, set, and manage FORGE configuration settings.",
}

// configGetCmd: forge config get [key]
var configGetCmd = &cobra.Command{
	Use:   "get [key]",
	Short: "Get configuration value",
	Long:  "Get a specific configuration value or all settings if no key is provided.",
	Args:  cobra.MaximumNArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		// If a specific key is requested
		if len(args) == 1 {
			key := args[0]
			value := viper.GetString(key)
			if value == "" {
				// Try environment variable
				envKey := "FORGE_" + key
				value = os.Getenv(envKey)
			}

			formatter := internal.NewFormatter(format, nil)
			if format == "json" {
				return formatter.WriteJSON(map[string]string{key: value})
			}
			formatter.Println(value)
			return nil
		}

		// Get all config
		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		config, err := client.GetConfig(ctx)
		if err != nil {
			// Fall back to local config
			config = &internal.Config{
				APIURL:   viper.GetString("api_url"),
				NodeID:   viper.GetString("node_id"),
				Domain:   viper.GetString("domain"),
				Settings: make(map[string]string),
			}
			if config.APIURL == "" {
				config.APIURL = internal.ResolveControlPlaneURL()
			}
			if config.NodeID == "" {
				config.NodeID = os.Getenv("FORGE_NODE_ID")
			}
			if config.Domain == "" {
				config.Domain = os.Getenv("FORGE_DOMAIN")
			}
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatConfig(config)
	},
}

// configSetCmd: forge config set <key> <value>
var configSetCmd = &cobra.Command{
	Use:   "set <key> <value>",
	Short: "Set configuration value",
	Long:  "Set a configuration value in the FORGE configuration file.",
	Args:  cobra.ExactArgs(2),
	RunE: func(cmd *cobra.Command, args []string) error {
		key := args[0]
		value := args[1]
		format, _ := cmd.Flags().GetString("format")

		// Set in viper
		viper.Set(key, value)

		// Determine config file path
		configFile := viper.ConfigFileUsed()
		if configFile == "" {
			// Use default location
			forgeRoot := os.Getenv("FORGE_ROOT")
			if forgeRoot == "" {
				forgeRoot = "."
			}
			configFile = filepath.Join(forgeRoot, ".forge", "forge.yaml")
		}

		// Ensure directory exists
		configDir := filepath.Dir(configFile)
		if err := os.MkdirAll(configDir, 0755); err != nil {
			return fmt.Errorf("failed to create config directory: %w", err)
		}

		// Write config
		if err := viper.WriteConfigAs(configFile); err != nil {
			return fmt.Errorf("failed to write config: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format != "json" && format != "quiet" {
			formatter.Printf("Set %s = %s\n", key, value)
		}
		return nil
	},
}

// configListCmd: forge config list
var configListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all configuration",
	Long:  "List all configuration settings.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		// Build config from various sources
		config := &internal.Config{
			APIURL: os.Getenv("FORGE_API_URL"),
			NodeID: os.Getenv("FORGE_NODE_ID"),
			Domain: os.Getenv("FORGE_DOMAIN"),
			Settings: map[string]string{
				"config_file": viper.ConfigFileUsed(),
				"api_url":     viper.GetString("api_url"),
				"node_id":     viper.GetString("node_id"),
				"domain":      viper.GetString("domain"),
				"log_level":   viper.GetString("log_level"),
			},
		}

		if config.APIURL == "" {
			config.APIURL = internal.ResolveControlPlaneURL()
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatConfig(config)
	},
}

func init() {
	// initCmd and envCmd visible — setup and configuration are user-facing
	// Add commands to config noun
	configCmd.AddCommand(configGetCmd)
	configCmd.AddCommand(configSetCmd)
	configCmd.AddCommand(configListCmd)

	// Register flags for initCmd
	initCmd.Flags().String("node-id", "", "Node ID (default: hostname)")
	initCmd.Flags().String("control-plane", internal.DefaultControlPlaneURL, "Control plane URL")
	initCmd.Flags().String("forge-root", "", "FORGE_ROOT directory")
	initCmd.Flags().Bool("agent", false, "Agent setup mode: print agent-specific quick start")
}

// initCmd represents the forge init command (ADR-030: writes ~/.forge/forge.toml for v3 daemon).
var initCmd = &cobra.Command{
	Use:   "init",
	Short: "Initialize FORGE configuration",
	Long:  "Setup wizard: creates ~/.forge/forge.toml for the v3 daemon (ADR-030).",
	RunE: func(cmd *cobra.Command, args []string) error {
		home, err := os.UserHomeDir()
		if err != nil {
			return fmt.Errorf("failed to get home dir: %w", err)
		}
		configPath := filepath.Join(home, ".forge", "forge.toml")
		configDir := filepath.Dir(configPath)

		if _, err := os.Stat(configPath); err == nil {
			fmt.Printf("Config already exists at %s\n", configPath)
			fmt.Print("Overwrite? [y/N]: ")
			scanner := bufio.NewScanner(os.Stdin)
			if !scanner.Scan() {
				return nil
			}
			if strings.TrimSpace(strings.ToLower(scanner.Text())) != "y" {
				fmt.Println("Skipped.")
				return nil
			}
		}

		fmt.Println("FORGE Setup Wizard")
		fmt.Println("==================")

		// Defaults
		defaultURL := internal.DefaultControlPlaneURL
		defaultNodeID := ""
		if h, _ := os.Hostname(); h != "" {
			defaultNodeID = h
		}

		// Auto-detect profile
		suggestedProfile := detectInitProfile(defaultNodeID)
		fmt.Printf("\nAuto-detected: hostname=%s, FORGE_ROOT=%s\n", defaultNodeID, os.Getenv("FORGE_ROOT"))
		fmt.Printf("\nWhat's your role?\n")
		fmt.Printf("  1) solo       — local dev, no fleet\n")
		fmt.Printf("  2) worker     — fleet agent, join a hub\n")
		fmt.Printf("  3) hub        — orchestrator, manage fleet\n")
		fmt.Printf("  4) portfolio  — business lead, full stack\n")
		profileMap := map[string]string{"1": "solo", "2": "worker", "3": "hub", "4": "portfolio"}
		profileDefault := "3"
		for k, v := range profileMap {
			if v == suggestedProfile {
				profileDefault = k
			}
		}
		fmt.Printf("\nChoice [%s]: ", profileDefault)
		profile := suggestedProfile
		if scanner := bufio.NewScanner(os.Stdin); scanner.Scan() {
			choice := strings.TrimSpace(scanner.Text())
			if choice == "" {
				choice = profileDefault
			}
			if p, ok := profileMap[choice]; ok {
				profile = p
			}
		}
		fmt.Printf("Profile: %s\n\n", profile)

		// Prompts (flags override when set)
		daemonURL, _ := cmd.Flags().GetString("control-plane")
		if daemonURL == "" || daemonURL == internal.DefaultControlPlaneURL {
			daemonURL = defaultURL
			// Worker nodes need to know hub address; hub defaults to localhost
			if profile == "worker" {
				fmt.Printf("Hub address [%s]: ", defaultURL)
			} else {
				fmt.Printf("Daemon URL [%s]: ", defaultURL)
			}
			if scanner := bufio.NewScanner(os.Stdin); scanner.Scan() && strings.TrimSpace(scanner.Text()) != "" {
				daemonURL = strings.TrimSpace(scanner.Text())
			}
		}

		nodeID, _ := cmd.Flags().GetString("node-id")
		if nodeID == "" {
			nodeID = defaultNodeID
			fmt.Printf("Node ID [%s]: ", defaultNodeID)
			if scanner := bufio.NewScanner(os.Stdin); scanner.Scan() && strings.TrimSpace(scanner.Text()) != "" {
				nodeID = strings.TrimSpace(scanner.Text())
			}
		}

		apiToken := ""
		if profile != "solo" {
			fmt.Print("API Token (leave empty for local mode): ")
			if scanner := bufio.NewScanner(os.Stdin); scanner.Scan() {
				apiToken = strings.TrimSpace(scanner.Text())
			}
		}

		port := 8081
		if u, err := url.Parse(daemonURL); err == nil && u.Port() != "" {
			if p, err := strconv.Atoi(u.Port()); err == nil {
				port = p
			}
		}
		wsPort := 8082
		dbPath := filepath.Join(home, ".forge", "forge-v3.db")
		forgeRoot := "."
		if r, _ := cmd.Flags().GetString("forge-root"); r != "" {
			forgeRoot = r
		} else if r := os.Getenv("FORGE_ROOT"); r != "" {
			forgeRoot = r
		} else if r := findForgeRoot(); r != "" {
			forgeRoot = r
		} else if cwd, _ := os.Getwd(); cwd != "" {
			forgeRoot = cwd
		}
		authMode := "local"
		if apiToken != "" {
			authMode = "api"
		}

		if err := os.MkdirAll(configDir, 0755); err != nil {
			return fmt.Errorf("failed to create config directory: %w", err)
		}

		configContent := fmt.Sprintf(`# FORGE v3 daemon config (ADR-030)
profile = %q

[daemon]
port = %d
ws_port = %d
db_path = %q
node_id = %q
forge_root = %q

[auth]
mode = %q
api_token = %q
`, profile, port, wsPort, dbPath, nodeID, forgeRoot, authMode, apiToken)

		if err := os.WriteFile(configPath, []byte(configContent), 0644); err != nil {
			return fmt.Errorf("failed to write config file: %w", err)
		}

		fmt.Printf("Writing %s... done.\n", configPath)
		fmt.Printf("\n✓ Config written to %s\n", configPath)
		fmt.Printf("✓ Profile: %s\n", profile)

		// Optional: also write legacy config.toml for viper/CLI
		legacyPath := filepath.Join(home, ".forge", "config.toml")
		legacyContent := fmt.Sprintf(`[node]
id = "%s"

[control_plane]
url = "%s"

[paths]
forge_root = "%s"
`, nodeID, daemonURL, forgeRoot)
		_ = os.WriteFile(legacyPath, []byte(legacyContent), 0644)

		fmt.Printf("\nNext steps:\n")
		switch profile {
		case "hub":
			fmt.Printf("  forge daemon start    # start the local daemon\n")
			fmt.Printf("  forge status          # verify fleet connectivity\n")
			fmt.Printf("  forge fleet windows   # see agent windows\n")
		case "worker":
			fmt.Printf("  forge work --daemon   # start autonomous task claim loop\n")
			fmt.Printf("  forge agent list      # verify you're registered\n")
		case "solo":
			fmt.Printf("  forge daemon start    # start the local daemon\n")
			fmt.Printf("  forge task create --title \"My first task\" --domain test --project demo --type feature\n")
		case "portfolio":
			fmt.Printf("  forge daemon start    # start the local daemon\n")
			fmt.Printf("  forge status          # fleet health snapshot\n")
			fmt.Printf("  forge approval list   # review pending approvals\n")
		default:
			fmt.Printf("  forge daemon start    # start the local daemon (uses %s)\n", configPath)
			fmt.Printf("  forge status          # verify fleet connectivity\n")
		}
		return nil
	},
}

// detectInitProfile suggests a role based on environment signals.
func detectInitProfile(hostname string) string {
	if agentType := os.Getenv("FORGE_AGENT_TYPE"); agentType == "fleet" {
		return "worker"
	}
	if p := os.Getenv("FORGE_PROFILE"); p != "" {
		return p
	}
	// Known orchestrator nodes default to hub
	knownHubs := map[string]bool{"prya": true, "sati": true, "nova": true, "gaea": true, "vega": true}
	if knownHubs[hostname] {
		return "hub"
	}
	return "hub" // safe default
}

// testControlPlaneConnection does a quick HTTP GET to the control plane health endpoint.
func testControlPlaneConnection(url string) bool {
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get(url + "/health")
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode == 200
}

// envCmd represents the forge env command
var envCmd = &cobra.Command{
	Use:   "env",
	Short: "Print FORGE environment variables",
	Long:  "Print shell-sourceable environment variables based on current configuration.",
	Run: func(cmd *cobra.Command, args []string) {
		nodeID := viper.GetString("node.id")
		if nodeID == "" {
			nodeID = viper.GetString("node_id")
		}
		if nodeID == "" {
			nodeID, _ = os.Hostname()
		}

		apiURL := internal.ResolveControlPlaneURL()

		forgeRoot := viper.GetString("paths.forge_root")
		if forgeRoot == "" {
			forgeRoot = os.Getenv("FORGE_ROOT")
		}
		if forgeRoot == "" {
			forgeRoot = findForgeRoot()
		}

		fmt.Printf("export FORGE_NODE_ID=%s\n", nodeID)
		fmt.Printf("export FORGE_API_URL=%s\n", apiURL)
		fmt.Printf("export FORGE_ROOT=%s\n", forgeRoot)
	},
}
