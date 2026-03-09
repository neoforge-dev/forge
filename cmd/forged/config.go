//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bufio"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// ForgeConfig is the ADR-030 config file structure (~/.forge/forge.toml).
type ForgeConfig struct {
	Daemon DaemonConfig   `toml:"daemon"`
	Auth   FileAuthConfig `toml:"auth"`
}

// DaemonConfig holds daemon server and paths.
type DaemonConfig struct {
	Port      int    `toml:"port"`       // default 8081
	WSPort    int    `toml:"ws_port"`    // default 8082
	DBPath    string `toml:"db_path"`    // default ~/.forge/forge-v3.db
	NodeID    string `toml:"node_id"`   // default os.Hostname()
	ForgeRoot string `toml:"forge_root"` // default .
}

// FileAuthConfig holds auth mode and token from forge.toml (not auth.AuthConfig).
type FileAuthConfig struct {
	Mode     string `toml:"mode"`      // local or api
	APIToken string `toml:"api_token"` // FORGE_API_TOKEN equivalent
}

// configPaths returns candidate paths for the config file (first found wins).
func configPaths() []string {
	home, _ := os.UserHomeDir()
	cwd, _ := os.Getwd()
	paths := []string{}
	if p := os.Getenv("FORGE_CONFIG"); p != "" {
		paths = append(paths, p)
	}
	if home != "" {
		paths = append(paths, filepath.Join(home, ".forge", "forge.toml"))
	}
	paths = append(paths, filepath.Join(cwd, ".forge", "forge.toml"))
	return paths
}

// loadForgeConfig loads config from first existing path; env vars override.
// If no file is found, returns a zero config (caller falls back to env).
func loadForgeConfig() ForgeConfig {
	for _, p := range configPaths() {
		if _, err := os.Stat(p); err != nil {
			continue
		}
		cfg, err := parseForgeTOML(p)
		if err != nil {
			continue
		}
		return cfg
	}
	return ForgeConfig{}
}

// parseForgeTOML does minimal TOML parsing for [daemon] and [auth] sections.
func parseForgeTOML(path string) (ForgeConfig, error) {
	f, err := os.Open(path)
	if err != nil {
		return ForgeConfig{}, err
	}
	defer f.Close()

	var cfg ForgeConfig
	section := ""
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			section = strings.Trim(line[1:len(line)-1], " ")
			continue
		}
		idx := strings.Index(line, "=")
		if idx <= 0 {
			continue
		}
		key := strings.TrimSpace(line[:idx])
		val := strings.TrimSpace(line[idx+1:])
		val = strings.Trim(val, "\"'")
		switch section {
		case "daemon":
			switch key {
			case "port":
				if n, err := strconv.Atoi(val); err == nil {
					cfg.Daemon.Port = n
				}
			case "ws_port":
				if n, err := strconv.Atoi(val); err == nil {
					cfg.Daemon.WSPort = n
				}
			case "db_path":
				cfg.Daemon.DBPath = val
			case "node_id":
				cfg.Daemon.NodeID = val
			case "forge_root":
				cfg.Daemon.ForgeRoot = val
			}
		case "auth":
			switch key {
			case "mode":
				cfg.Auth.Mode = val
			case "api_token":
				cfg.Auth.APIToken = val
			}
		}
	}
	return cfg, scanner.Err()
}
