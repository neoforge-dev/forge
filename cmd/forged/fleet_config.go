//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"gopkg.in/yaml.v3"
)

// fleetConfig holds the fleet-level configuration from domains.yaml.
type fleetConfig struct {
	NoAutoAssignAgents []string `yaml:"no_auto_assign_agents"`
}

// domainEntry represents a single domain in the domains.yaml file.
type domainEntry struct {
	DisplayName  string `yaml:"display_name"`
	Owner        string `yaml:"owner"`
	Active       bool   `yaml:"active"`
	FrontendTier string `yaml:"frontend_tier"`
}

// fleetRegistry is the top-level structure parsed from domains.yaml.
type fleetRegistry struct {
	Fleet   fleetConfig            `yaml:"fleet"`
	Domains map[string]domainEntry `yaml:"domains"`
}

// fleetConfigCache caches the parsed registry and tracks file modification time.
type fleetConfigCache struct {
	mu       sync.RWMutex
	registry *fleetRegistry
	modTime  time.Time
	path     string
}

var globalFleetCache = &fleetConfigCache{}

// fleetConfigPath returns the path to config/domains.yaml relative to FORGE_ROOT.
func fleetConfigPath() string {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	return filepath.Join(root, "config", "domains.yaml")
}

// loadFleetConfig reads and caches the fleet registry from domains.yaml.
// It reloads only when the file has been modified since the last read.
func loadFleetConfig() *fleetRegistry {
	path := fleetConfigPath()

	globalFleetCache.mu.RLock()
	cached := globalFleetCache.registry
	cachedMod := globalFleetCache.modTime
	cachedPath := globalFleetCache.path
	globalFleetCache.mu.RUnlock()

	// Check if file has changed.
	info, err := os.Stat(path)
	if err != nil {
		if cached != nil && cachedPath == path {
			return cached
		}
		log.Printf("[fleet_config] cannot stat %s: %v — returning empty registry", path, err)
		return &fleetRegistry{}
	}

	if cached != nil && cachedPath == path && !info.ModTime().After(cachedMod) {
		return cached
	}

	// Reload.
	data, err := os.ReadFile(path)
	if err != nil {
		log.Printf("[fleet_config] cannot read %s: %v — returning empty registry", path, err)
		if cached != nil {
			return cached
		}
		return &fleetRegistry{}
	}

	var reg fleetRegistry
	if err := yaml.Unmarshal(data, &reg); err != nil {
		log.Printf("[fleet_config] cannot parse %s: %v — returning empty registry", path, err)
		if cached != nil {
			return cached
		}
		return &fleetRegistry{}
	}

	globalFleetCache.mu.Lock()
	globalFleetCache.registry = &reg
	globalFleetCache.modTime = info.ModTime()
	globalFleetCache.path = path
	globalFleetCache.mu.Unlock()

	return &reg
}

// NoAutoAssignAgents returns the list of agent IDs that should not be
// auto-assigned worker tasks (typically orchestrator/control-plane nodes).
func NoAutoAssignAgents() []string {
	reg := loadFleetConfig()
	return reg.Fleet.NoAutoAssignAgents
}

// IsAgentAutoAssignable returns true if the given agent ID is NOT in the
// no-auto-assign exclusion list. The check is case-insensitive.
func IsAgentAutoAssignable(agentID string) bool {
	excluded := NoAutoAssignAgents()
	lower := strings.ToLower(agentID)
	for _, e := range excluded {
		if strings.ToLower(e) == lower {
			return false
		}
	}
	return true
}

// IsDomainActive returns whether the given domain is marked active in
// domains.yaml. Unknown domains (not present in the config) are treated
// as active to avoid blocking tasks for newly added domains.
func IsDomainActive(domain string) bool {
	reg := loadFleetConfig()
	if reg.Domains == nil {
		return true
	}
	entry, ok := reg.Domains[domain]
	if !ok {
		return true // unknown domain → treat as active
	}
	return entry.Active
}
