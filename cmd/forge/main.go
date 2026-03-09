package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
	"github.com/spf13/viper"
)

var (
	cfgFile      string
	cfg          *Config
	outputFormat string
)

// Config represents the forge configuration (legacy, used by worker/control-plane)
type Config struct {
	ControlPlane ControlPlaneConfig `yaml:"control_plane,omitempty" mapstructure:"control_plane"`
	Worker       WorkerConfig       `yaml:"worker,omitempty" mapstructure:"worker"`
	APIURL       string             `yaml:"api_url,omitempty" mapstructure:"api_url"`
	NodeID       string             `yaml:"node_id,omitempty" mapstructure:"node_id"`
	Domain       string             `yaml:"domain,omitempty" mapstructure:"domain"`
	LogLevel     string             `yaml:"log_level,omitempty" mapstructure:"log_level"`
}

// ControlPlaneConfig holds control-plane specific settings
type ControlPlaneConfig struct {
	Host     string `yaml:"host,omitempty" mapstructure:"host"`
	Port     int    `yaml:"port,omitempty" mapstructure:"port"`
	DataDir  string `yaml:"data_dir,omitempty" mapstructure:"data_dir"`
	Database string `yaml:"database,omitempty" mapstructure:"database"`
}

// WorkerConfig holds worker-specific settings
type WorkerConfig struct {
	ID            string   `yaml:"id,omitempty" mapstructure:"id"`
	ControlPlane  string   `yaml:"control_plane,omitempty" mapstructure:"control_plane"`
	Capabilities  []string `yaml:"capabilities,omitempty" mapstructure:"capabilities"`
	MaxConcurrent int      `yaml:"max_concurrent,omitempty" mapstructure:"max_concurrent"`
}

func main() {
	if err := rootCmd.Execute(); err != nil {
		// Check if this is an "unknown command" — try plugin dispatch
		msg := err.Error()
		if strings.HasPrefix(msg, "unknown command") {
			// Extract the unknown noun from: `unknown command "foo" for "forge"`
			parts := strings.Fields(msg)
			if len(parts) >= 3 {
				noun := strings.Trim(parts[2], `"`)
				// Try plugin dispatch with remaining os.Args
				for i, arg := range os.Args[1:] {
					if arg == noun {
						dispatchToPlugin(noun, os.Args[i+2:])
						break
					}
				}
			}
		}
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}

func init() {
	cobra.OnInitialize(initConfig)

	// Global flags
	rootCmd.PersistentFlags().StringVar(&cfgFile, "config", "", "config file (default: .forge/forge.yaml)")
	rootCmd.PersistentFlags().StringP("log-level", "l", "info", "log level (debug, info, warn, error)")
	rootCmd.PersistentFlags().StringVar(&outputFormat, "format", "table", "output format (table, json, csv, quiet)")

	viper.BindPFlag("log_level", rootCmd.PersistentFlags().Lookup("log-level"))
	viper.BindPFlag("format", rootCmd.PersistentFlags().Lookup("format"))

	// Add subcommands (nouns) - V4 style
	rootCmd.AddCommand(taskCmd)
	rootCmd.AddCommand(agentCmd)
	rootCmd.AddCommand(dispatchCmd)
	rootCmd.AddCommand(domainCmd)
	rootCmd.AddCommand(projectCmd)
	rootCmd.AddCommand(portfolioCmd)
	rootCmd.AddCommand(contextCmd)
	rootCmd.AddCommand(queueCmd)
	rootCmd.AddCommand(fleetCmd)
	rootCmd.AddCommand(approvalCmd)
	rootCmd.AddCommand(laneCmd)
	rootCmd.AddCommand(patternCmd)
	rootCmd.AddCommand(patrolCmd)
	rootCmd.AddCommand(configCmd)
	rootCmd.AddCommand(daemonCmd)
	rootCmd.AddCommand(handoffCmd)
	rootCmd.AddCommand(councilCmd)
	rootCmd.AddCommand(statusCmd)
	rootCmd.AddCommand(doctorCmd)
	rootCmd.AddCommand(versionCmd)
	rootCmd.AddCommand(initCmd)
	rootCmd.AddCommand(envCmd)

	// Plugin management
	rootCmd.AddCommand(pluginCmd)

	// Shell completion
	rootCmd.AddCommand(completionCmd)

	// Node management (XNode mesh)
	rootCmd.AddCommand(nodeCmd)

	// Git operations (fleet-safe push with retry)
	rootCmd.AddCommand(gitCmd)

	// Lead cross-node XNode messaging
	rootCmd.AddCommand(leadCmd)

	// Relay — dispatch relay worker
	rootCmd.AddCommand(relayCmd)

	// Self-update: rebuild the forge CLI binary in-place
	rootCmd.AddCommand(selfUpdateCmd)

	// Heartbeat — top-level convenience command (replaces hb-loop bash script)
	rootCmd.AddCommand(heartbeatCmd)

	// State — lead orchestrator FSM state management (ports state_manager.py)
	rootCmd.AddCommand(stateCmd)

	// Lock — multi-scope file lock manager (ports .forge/scripts/git-lock.sh)
	rootCmd.AddCommand(lockCmd)

	// Legacy commands (control-plane, worker) - keep for backwards compatibility
	rootCmd.AddCommand(controlPlaneCmd)
	rootCmd.AddCommand(workerCmd)

	cobra.EnableCommandSorting = false
}

// completionCmd: forge completion [bash|zsh|fish|powershell]
var completionCmd = &cobra.Command{
	Use:   "completion [bash|zsh|fish|powershell]",
	Short: "Generate shell completion script",
	Long: `Generate shell completion script for forge.

To load completions:

  Bash:
    source <(forge completion bash)
    # Persist: echo 'source <(forge completion bash)' >> ~/.bashrc

  Zsh:
    forge completion zsh > "${fpath[1]}/_forge"
    # Or: source <(forge completion zsh)

  Fish:
    forge completion fish | source

  PowerShell:
    forge completion powershell | Out-String | Invoke-Expression`,
	DisableFlagsInUseLine: true,
	ValidArgs:             []string{"bash", "zsh", "fish", "powershell"},
	Args:                  cobra.MatchAll(cobra.ExactArgs(1), cobra.OnlyValidArgs),
	RunE: func(cmd *cobra.Command, args []string) error {
		switch args[0] {
		case "bash":
			return rootCmd.GenBashCompletion(os.Stdout)
		case "zsh":
			return rootCmd.GenZshCompletion(os.Stdout)
		case "fish":
			return rootCmd.GenFishCompletion(os.Stdout, true)
		case "powershell":
			return rootCmd.GenPowerShellCompletionWithDesc(os.Stdout)
		default:
			return fmt.Errorf("unknown shell: %s", args[0])
		}
	},
}

// handleUnknownCommand checks if an unknown command matches a forge-<noun> plugin.
// Called from rootCmd.PersistentPreRun when the command is not found.
func handleUnknownCommand(args []string) {
	if len(args) == 0 {
		return
	}
	noun := args[0]
	// Skip if it looks like a flag
	if strings.HasPrefix(noun, "-") {
		return
	}
	if dispatchToPlugin(noun, args[1:]) {
		// dispatchToPlugin calls os.Exit, so this is unreachable
		return
	}
	// Not found — cobra will show "unknown command" error
}

func initConfig() {
	// Precedence: CLI flags > ENV vars > ~/.forge/config.toml > project .forge/forge.yaml > defaults

	// 1. Set environment prefix and automatic env mapping
	viper.SetEnvPrefix("FORGE")
	viper.AutomaticEnv()

	// 2. Set defaults
	viper.SetDefault("control_plane.host", "0.0.0.0")
	viper.SetDefault("control_plane.port", 8080)
	viper.SetDefault("control_plane.data_dir", ".forge/data")
	viper.SetDefault("control_plane.database", "sqlite")
	viper.SetDefault("worker.max_concurrent", 5)
	viper.SetDefault("api_url", "http://localhost:8081")
	viper.SetDefault("log_level", "info")
	viper.SetDefault("format", "table")

	// 3. Project-scoped config: .forge/forge.yaml (Lowest file priority)
	if cfgFile == "" {
		forgeRoot := os.Getenv("FORGE_ROOT")
		if forgeRoot == "" {
			forgeRoot = findForgeRoot()
		}
		if forgeRoot != "" {
			projectCfg := filepath.Join(forgeRoot, ".forge", "forge.yaml")
			if _, err := os.Stat(projectCfg); err == nil {
				viper.SetConfigFile(projectCfg)
				if err := viper.ReadInConfig(); err == nil {
					// Successfully read project config
				}
			}
		}
	} else {
		// Explicit --config flag always wins
		viper.SetConfigFile(cfgFile)
		viper.ReadInConfig()
	}

	// 4. User-level config: ~/.forge/config.toml (Higher priority than project config)
	// We merge this so it overwrites project config values
	home, _ := os.UserHomeDir()
	userCfg := filepath.Join(home, ".forge", "config.toml")
	if _, err := os.Stat(userCfg); err == nil {
		f, err := os.Open(userCfg)
		if err == nil {
			defer f.Close()
			viper.SetConfigType("toml")
			if err := viper.MergeConfig(f); err == nil {
				// Successfully merged user config
			}
		}
	}

	// Unmarshal into struct
	cfg = &Config{}
	if err := viper.Unmarshal(cfg); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: failed to unmarshal config: %v\n", err)
	}
}

// findForgeRoot attempts to find the FORGE_ROOT by traversing up looking for .forge directory
func findForgeRoot() string {
	dir, err := os.Getwd()
	if err != nil {
		return ""
	}

	for {
		forgeDir := filepath.Join(dir, ".forge")
		if info, err := os.Stat(forgeDir); err == nil && info.IsDir() {
			return dir
		}

		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}

	return ""
}

var rootCmd = &cobra.Command{
	Use:           "forge",
	Short:         "FORGE - Fleet Operations and Resource Governance Engine",
	SilenceErrors: true,
	Long: `
╔════════════════════════════════════════════════════════════════╗
║                    FORGE v4.0                                  ║
║         Fleet Operations and Resource Governance              ║
╚════════════════════════════════════════════════════════════════╝

QUICK START:
  forge status                    # Check system health
  forge task list                 # List all tasks
  forge task create --title "..." # Create a new task
  forge agent list                # List connected agents
  forge daemon status             # Check daemon status

CORE NOUNS:
  task         Manage tasks (create, list, update, delete, claim, complete)
  agent        Manage agents (list, show, tasks)
  dispatch     Send work to agents (send, show)
  domain       Manage domains (list, show, create)
  project      Manage projects (list, show, create)
  context      Manage knowledge contexts (list, show, create, envelope)
  approval     Manage approvals (list, show, decide)
  lane         Manage Dark Factory lanes (list, show, promote)
  config       Manage configuration (get, set, list)
  daemon       Manage daemon (start, stop, status)
  plugin       Manage CLI plugins (list, install, remove, info)
  node         Manage XNode mesh nodes (list, status)

PLUGINS:
  Any forge-<noun> binary in $PATH or ~/.forge/plugins/ is callable as:
    forge <noun> [args]    →    forge-<noun> [args]

GLOBAL FLAGS:
  --format=table    Output format (table, json, csv, quiet)
  --config          Config file path
  -l, --log-level   Log level (debug, info, warn, error)

Use "forge <noun> --help" for more information about a command.
`,
	SilenceUsage: true,
	Run: func(cmd *cobra.Command, args []string) {
		// Show help if no subcommand
		cmd.Help()
	},
}

// statusCmd: forge status — morning standup fleet health snapshot (Task #48)
var statusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show fleet health snapshot",
	Long:  "Show a comprehensive fleet health snapshot: daemon, agents, queue, commits, patrols, pending results.",
	RunE:  statusCmdImpl,
}

// versionCmd: forge version
var versionCmd = &cobra.Command{
	Use:   "version",
	Short: "Show version information",
	Long:  "Display version information about the FORGE CLI.",
	Run: func(cmd *cobra.Command, args []string) {
		format, _ := cmd.Flags().GetString("format")

		version := "4.0.0"
		commit := "unknown"

		if format == "json" {
			fmt.Printf(`{"version": "%s", "commit": "%s"}`+"\n", version, commit)
		} else {
			fmt.Printf("FORGE CLI v%s (commit: %s)\n", version, commit)
		}
	},
}
