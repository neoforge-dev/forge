// plugin.go - forge plugin subcommand (ADR-031)
//
// Implements git-style subprocess plugin dispatch:
//   forge <noun>  →  searches PATH + ~/.forge/plugins/ for forge-<noun>
//
// Commands:
//   forge plugin list             - list installed plugins
//   forge plugin install <path>   - install plugin from file or URL
//   forge plugin remove <name>    - remove an installed plugin
//   forge plugin info <name>      - show plugin metadata

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/pelletier/go-toml/v2"
	"github.com/spf13/cobra"
)

// PluginEntry is a single entry in the plugin registry.
type PluginEntry struct {
	Name    string `toml:"name"`
	Binary  string `toml:"binary"`
	Version string `toml:"version"`
	Source  string `toml:"source"`
}

// PluginRegistry is the parsed registry.toml structure.
type PluginRegistry struct {
	Plugin []PluginEntry `toml:"plugin"`
}

var pluginCmd = &cobra.Command{
	Use:   "plugin",
	Short: "Manage FORGE plugins",
	Long: `Manage FORGE CLI plugins.

Plugins are executables named forge-{noun} in $PATH or ~/.forge/plugins/.
They are invoked automatically when you run: forge {noun} [args]

Examples:
  forge plugin list              # list installed plugins
  forge plugin install ./forge-ios  # install a plugin from local path
  forge plugin info ios          # show plugin metadata
`,
}

// pluginListCmd lists installed plugins.
var pluginListCmd = &cobra.Command{
	Use:   "list",
	Short: "List installed plugins",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		plugins, err := loadPluginRegistry()
		if err != nil {
			return fmt.Errorf("failed to load plugin registry: %w", err)
		}

		// Also discover plugins in PATH that aren't registered
		discovered := discoverPluginsInPath()
		registered := make(map[string]bool)
		for _, p := range plugins {
			registered[p.Binary] = true
		}
		for _, name := range discovered {
			if !registered[name] {
				plugins = append(plugins, PluginEntry{
					Name:    strings.TrimPrefix(name, "forge-"),
					Binary:  name,
					Version: "unknown",
					Source:  "discovered",
				})
			}
		}

		if format == "json" {
			data, _ := json.MarshalIndent(plugins, "", "  ")
			fmt.Println(string(data))
			return nil
		}

		if len(plugins) == 0 {
			fmt.Println("No plugins installed.")
			fmt.Printf("\nTo install a plugin: forge plugin install <path-or-url>\n")
			return nil
		}

		fmt.Printf("%-20s %-12s %-12s %s\n", "NAME", "VERSION", "SOURCE", "BINARY")
		fmt.Println(strings.Repeat("─", 72))
		for _, p := range plugins {
			fmt.Printf("%-20s %-12s %-12s %s\n", p.Name, p.Version, p.Source, p.Binary)
		}
		return nil
	},
}

// pluginInstallCmd installs a plugin from a local path.
var pluginInstallCmd = &cobra.Command{
	Use:   "install <path>",
	Short: "Install a plugin",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		src := args[0]

		pluginsDir, err := ensurePluginsDir()
		if err != nil {
			return err
		}

		// Resolve source binary name
		binaryName := filepath.Base(src)
		if !strings.HasPrefix(binaryName, "forge-") {
			return fmt.Errorf("plugin binary must be named forge-<noun> (got: %s)", binaryName)
		}
		noun := strings.TrimPrefix(binaryName, "forge-")
		destPath := filepath.Join(pluginsDir, binaryName)

		// Copy file
		srcData, err := os.ReadFile(src)
		if err != nil {
			return fmt.Errorf("cannot read plugin file %s: %w", src, err)
		}
		if err := os.WriteFile(destPath, srcData, 0755); err != nil {
			return fmt.Errorf("cannot write plugin to %s: %w", destPath, err)
		}

		// Query plugin for its metadata
		version := "unknown"
		if info, err := getPluginInfo(destPath); err == nil {
			if v, ok := info["version"].(string); ok {
				version = v
			}
		}

		// Register in registry.toml
		if err := registerPlugin(PluginEntry{
			Name:    noun,
			Binary:  binaryName,
			Version: version,
			Source:  src,
		}); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: could not update registry: %v\n", err)
		}

		fmt.Printf("Installed plugin: forge %s (binary: %s)\n", noun, destPath)
		return nil
	},
}

// pluginRemoveCmd removes a plugin.
var pluginRemoveCmd = &cobra.Command{
	Use:   "remove <name>",
	Short: "Remove a plugin",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		noun := args[0]
		binaryName := "forge-" + noun

		pluginsDir, err := ensurePluginsDir()
		if err != nil {
			return err
		}

		destPath := filepath.Join(pluginsDir, binaryName)
		if err := os.Remove(destPath); err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("failed to remove plugin binary: %w", err)
		}

		if err := unregisterPlugin(noun); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: could not update registry: %v\n", err)
		}

		fmt.Printf("Removed plugin: forge %s\n", noun)
		return nil
	},
}

// pluginInfoCmd shows plugin metadata.
var pluginInfoCmd = &cobra.Command{
	Use:   "info <name>",
	Short: "Show plugin information",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		noun := args[0]
		binaryPath := findPlugin(noun)
		if binaryPath == "" {
			return fmt.Errorf("plugin 'forge-%s' not found in PATH or ~/.forge/plugins/\n\nInstall with: forge plugin install ./forge-%s", noun, noun)
		}

		info, err := getPluginInfo(binaryPath)
		if err != nil {
			return fmt.Errorf("plugin found at %s but --forge-plugin-info failed: %w\n\nThe plugin may not implement the required interface.", binaryPath, err)
		}

		data, _ := json.MarshalIndent(info, "", "  ")
		fmt.Println(string(data))
		return nil
	},
}

func init() {
	pluginCmd.AddCommand(pluginListCmd)
	pluginCmd.AddCommand(pluginInstallCmd)
	pluginCmd.AddCommand(pluginRemoveCmd)
	pluginCmd.AddCommand(pluginInfoCmd)
}

// dispatchToPlugin looks for forge-<noun> in ~/.forge/plugins/ then $PATH.
// Returns true if the plugin was found and executed (caller should exit).
// args is everything after the noun (the subcommand args).
func dispatchToPlugin(noun string, args []string) bool {
	binaryPath := findPlugin(noun)
	if binaryPath == "" {
		return false
	}

	// Inject FORGE context env vars for the plugin
	env := os.Environ()
	env = appendIfMissing(env, "FORGE_CONTROL_PLANE", getControlPlaneURL())
	env = appendIfMissing(env, "FORGE_NODE_ID", getNodeID())
	env = appendIfMissing(env, "FORGE_TOKEN", getAuthToken())
	env = appendIfMissing(env, "FORGE_OUTPUT", outputFormat)

	cmdExec := exec.Command(binaryPath, args...)
	cmdExec.Stdout = os.Stdout
	cmdExec.Stderr = os.Stderr
	cmdExec.Stdin = os.Stdin
	cmdExec.Env = env

	if err := cmdExec.Run(); err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			os.Exit(exitErr.ExitCode())
		}
		fmt.Fprintf(os.Stderr, "Error: plugin forge-%s failed: %v\n", noun, err)
		os.Exit(1)
	}
	os.Exit(0)
	return true // unreachable, satisfies compiler
}

// findPlugin searches for forge-<noun> binary in ~/.forge/plugins/ then $PATH.
func findPlugin(noun string) string {
	binaryName := "forge-" + noun

	// 1. Check ~/.forge/plugins/
	if home, err := os.UserHomeDir(); err == nil {
		localPath := filepath.Join(home, ".forge", "plugins", binaryName)
		if isExecutable(localPath) {
			return localPath
		}
	}

	// 2. Check $PATH
	if path, err := exec.LookPath(binaryName); err == nil {
		return path
	}

	return ""
}

// discoverPluginsInPath finds all forge-* executables in PATH.
func discoverPluginsInPath() []string {
	var found []string
	seen := make(map[string]bool)

	for _, dir := range filepath.SplitList(os.Getenv("PATH")) {
		entries, err := os.ReadDir(dir)
		if err != nil {
			continue
		}
		for _, e := range entries {
			name := e.Name()
			if strings.HasPrefix(name, "forge-") && !seen[name] {
				fullPath := filepath.Join(dir, name)
				if isExecutable(fullPath) {
					found = append(found, name)
					seen[name] = true
				}
			}
		}
	}

	// Also check ~/.forge/plugins/
	if home, err := os.UserHomeDir(); err == nil {
		pluginsDir := filepath.Join(home, ".forge", "plugins")
		entries, err := os.ReadDir(pluginsDir)
		if err == nil {
			for _, e := range entries {
				name := e.Name()
				if strings.HasPrefix(name, "forge-") && !seen[name] {
					fullPath := filepath.Join(pluginsDir, name)
					if isExecutable(fullPath) {
						found = append(found, name)
						seen[name] = true
					}
				}
			}
		}
	}

	return found
}

func isExecutable(path string) bool {
	info, err := os.Stat(path)
	if err != nil {
		return false
	}
	return !info.IsDir() && info.Mode()&0111 != 0
}

func pluginsRegistryPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".forge", "plugins", "registry.toml"), nil
}

func ensurePluginsDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("cannot determine home directory: %w", err)
	}
	dir := filepath.Join(home, ".forge", "plugins")
	if err := os.MkdirAll(dir, 0755); err != nil {
		return "", fmt.Errorf("cannot create plugins directory: %w", err)
	}
	return dir, nil
}

func loadPluginRegistry() ([]PluginEntry, error) {
	regPath, err := pluginsRegistryPath()
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(regPath)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var reg PluginRegistry
	if err := toml.Unmarshal(data, &reg); err != nil {
		return nil, err
	}
	return reg.Plugin, nil
}

func registerPlugin(entry PluginEntry) error {
	plugins, _ := loadPluginRegistry()
	// Remove existing entry with same name
	filtered := plugins[:0]
	for _, p := range plugins {
		if p.Name != entry.Name {
			filtered = append(filtered, p)
		}
	}
	filtered = append(filtered, entry)

	reg := PluginRegistry{Plugin: filtered}
	data, err := toml.Marshal(reg)
	if err != nil {
		return err
	}

	regPath, err := pluginsRegistryPath()
	if err != nil {
		return err
	}
	return os.WriteFile(regPath, data, 0644)
}

func unregisterPlugin(noun string) error {
	plugins, _ := loadPluginRegistry()
	filtered := plugins[:0]
	for _, p := range plugins {
		if p.Name != noun {
			filtered = append(filtered, p)
		}
	}
	reg := PluginRegistry{Plugin: filtered}
	data, err := toml.Marshal(reg)
	if err != nil {
		return err
	}
	regPath, err := pluginsRegistryPath()
	if err != nil {
		return err
	}
	return os.WriteFile(regPath, data, 0644)
}

func getPluginInfo(binaryPath string) (map[string]interface{}, error) {
	out, err := exec.Command(binaryPath, "--forge-plugin-info").Output()
	if err != nil {
		return nil, err
	}
	var info map[string]interface{}
	if err := json.Unmarshal(out, &info); err != nil {
		return nil, err
	}
	return info, nil
}

func appendIfMissing(env []string, key, value string) []string {
	prefix := key + "="
	for _, e := range env {
		if strings.HasPrefix(e, prefix) {
			return env
		}
	}
	return append(env, key+"="+value)
}

func getControlPlaneURL() string {
	if url := os.Getenv("FORGE_API_URL"); url != "" {
		return url
	}
	if cfg != nil && cfg.Worker.ControlPlane != "" {
		return cfg.Worker.ControlPlane
	}
	return "http://localhost:8081"
}

func getNodeID() string {
	if id := os.Getenv("FORGE_NODE_ID"); id != "" {
		return id
	}
	if cfg != nil && cfg.NodeID != "" {
		return cfg.NodeID
	}
	h, _ := os.Hostname()
	return h
}

func getAuthToken() string {
	if tok := os.Getenv("FORGE_TOKEN"); tok != "" {
		return tok
	}
	if tok := os.Getenv("FORGE_WEBHOOK_TOKEN"); tok != "" {
		return tok
	}
	return ""
}
