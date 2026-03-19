//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"gopkg.in/yaml.v3"
)

// StageGatePolicy defines what task types are allowed in each portfolio stage
// and what product fields must be present to operate in that stage.
type StageGatePolicy struct {
	// RequiredFields lists portfolio-state.yaml product fields that must be
	// non-empty (or true for booleans) for the gate to be satisfied.
	RequiredFields []string
	// AllowedTaskTypes is the allowlist of task type values for this stage.
	// Comparison is case-insensitive substring match: a task type "coverage"
	// matches an allowed type "test" because "coverage" → inferWorkload → "go-test"
	// but we keep simple string matching here for clarity.
	AllowedTaskTypes []string
	// BlockMessage is the human-readable reason returned when a task type is
	// not permitted in this stage.
	BlockMessage string
}

// stageGatePolicies maps lowercase portfolio stage names to their policies.
var stageGatePolicies = map[string]StageGatePolicy{
	"idea": {
		RequiredFields:   []string{"icp", "name"},
		AllowedTaskTypes: []string{"research", "validation", "docs"},
		BlockMessage:     "Product is in 'idea' stage. Only research/validation/docs tasks allowed. Move to 'validate' stage first.",
	},
	"validate": {
		RequiredFields:   []string{"icp", "validation_hypothesis", "primary_risk"},
		AllowedTaskTypes: []string{"research", "validation", "prototype", "docs"},
		BlockMessage:     "Product is in 'validate' stage. No full build tasks until validation hypothesis is confirmed. Move to 'build' stage first.",
	},
	"build": {
		RequiredFields:   []string{"icp", "primary_metric"},
		AllowedTaskTypes: []string{"feature", "bug", "test", "coverage", "refactor", "docs", "research"},
		BlockMessage:     "Build stage requires ICP and primary_metric defined in portfolio-state.yaml.",
	},
	"deploy": {
		RequiredFields:   []string{"deploy_ready", "analytics_ready"},
		AllowedTaskTypes: []string{"deploy", "config", "monitoring", "docs", "bug", "test", "coverage"},
		BlockMessage:     "Deploy stage requires deploy_ready and analytics_ready fields set in portfolio-state.yaml.",
	},
	"measure": {
		RequiredFields:   []string{},
		AllowedTaskTypes: []string{"analytics", "experiment", "bug", "optimization", "research", "docs", "test", "coverage"},
		BlockMessage:     "Measure stage: focus on metrics and optimization. Build/deploy tasks are not appropriate here.",
	},
	"monetize": {
		RequiredFields:   []string{},
		AllowedTaskTypes: []string{"feature", "bug", "test", "coverage", "docs", "config", "analytics", "research"},
		BlockMessage:     "Monetize stage: focus on revenue features and conversion optimization.",
	},
	"scale": {
		RequiredFields:   []string{},
		AllowedTaskTypes: []string{"feature", "bug", "test", "coverage", "refactor", "docs", "config", "analytics", "research", "optimization"},
		BlockMessage:     "Scale stage: all standard task types are allowed.",
	},
	"kill": {
		RequiredFields:   []string{},
		AllowedTaskTypes: []string{"docs", "bug"},
		BlockMessage:     "Product is in 'kill' stage. Only bug fixes and documentation are permitted.",
	},
}

// infrastructureDomains are domains whose tasks always bypass stage gates.
// FORGE internal infra work should never be blocked by portfolio product rules.
var infrastructureDomains = map[string]bool{
	"infrastructure": true,
	"forge":          true,
	"forged":         true,
	"openclaw":       true,
	"github":         true,
}

// portfolioProduct is the parsed representation of a single product entry
// in portfolio-state.yaml. Only the fields referenced by stage gate checks
// need to be captured here.
type portfolioProduct struct {
	Key                   string `yaml:"key"`
	Name                  string `yaml:"name"`
	Domain                string `yaml:"domain"`
	Stage                 string `yaml:"stage"`
	ICP                   string `yaml:"icp"`
	PrimaryMetric         string `yaml:"primary_metric"`
	PrimaryRisk           string `yaml:"primary_risk"`
	ValidationHypothesis  string `yaml:"validation_hypothesis"`
	KillCriteria          string `yaml:"kill_criteria"`
	DistributionChannel   string `yaml:"distribution_channel"`
	DeployReady           bool   `yaml:"deploy_ready"`
	AnalyticsReady        bool   `yaml:"analytics_ready"`
}

// portfolioState is the top-level portfolio-state.yaml structure.
type portfolioState struct {
	Version  string             `yaml:"version"`
	Products []portfolioProduct `yaml:"products"`
}

// portfolioCache holds a parsed and cached copy of portfolio-state.yaml.
type portfolioCache struct {
	mu          sync.RWMutex
	state       *portfolioState
	loadedAt    time.Time
	filePath    string
	fileModTime time.Time
}

// global cache instance — lazily initialised on first use.
var globalPortfolioCache portfolioCache

// portfolioConfigPath returns the absolute path to portfolio-state.yaml,
// respecting FORGE_ROOT if set.
func portfolioConfigPath() string {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	return filepath.Join(root, "config", "portfolio", "portfolio-state.yaml")
}

// loadPortfolioState returns the parsed portfolio state, reloading from disk
// if the file has been modified since the last load.
func loadPortfolioState() (*portfolioState, error) {
	path := portfolioConfigPath()

	// Fast path: check mod-time under read lock.
	globalPortfolioCache.mu.RLock()
	if globalPortfolioCache.state != nil && globalPortfolioCache.filePath == path {
		info, err := os.Stat(path)
		if err == nil && !info.ModTime().After(globalPortfolioCache.fileModTime) {
			s := globalPortfolioCache.state
			globalPortfolioCache.mu.RUnlock()
			return s, nil
		}
	}
	globalPortfolioCache.mu.RUnlock()

	// Slow path: reload from disk under write lock.
	globalPortfolioCache.mu.Lock()
	defer globalPortfolioCache.mu.Unlock()

	data, err := os.ReadFile(path)
	if err != nil {
		// If the file doesn't exist, return an empty state (don't block tasks).
		if os.IsNotExist(err) {
			return &portfolioState{}, nil
		}
		return nil, fmt.Errorf("read portfolio-state.yaml: %w", err)
	}

	var state portfolioState
	if err := yaml.Unmarshal(data, &state); err != nil {
		return nil, fmt.Errorf("parse portfolio-state.yaml: %w", err)
	}

	info, _ := os.Stat(path)
	globalPortfolioCache.state = &state
	globalPortfolioCache.filePath = path
	globalPortfolioCache.loadedAt = time.Now()
	if info != nil {
		globalPortfolioCache.fileModTime = info.ModTime()
	}

	return &state, nil
}

// findProductByDomain returns the first product whose domain matches domainKey,
// or nil if no match is found.
func findProductByDomain(state *portfolioState, domainKey string) *portfolioProduct {
	dk := strings.ToLower(domainKey)
	for i := range state.Products {
		if strings.ToLower(state.Products[i].Domain) == dk {
			return &state.Products[i]
		}
	}
	return nil
}

// findProductByKey returns the product with the given key, or nil.
func findProductByKey(state *portfolioState, key string) *portfolioProduct {
	k := strings.ToLower(key)
	for i := range state.Products {
		if strings.ToLower(state.Products[i].Key) == k {
			return &state.Products[i]
		}
	}
	return nil
}

// taskTypeAllowed returns true when taskType (case-insensitive) appears in the
// allowed list, or when the allowed list is empty (no restriction).
func taskTypeAllowed(policy StageGatePolicy, taskType string) bool {
	if len(policy.AllowedTaskTypes) == 0 {
		return true
	}
	tt := strings.ToLower(strings.TrimSpace(taskType))
	for _, allowed := range policy.AllowedTaskTypes {
		if strings.EqualFold(allowed, tt) {
			return true
		}
	}
	return false
}

// checkRequiredFields returns a list of field names that are required by the
// policy but missing (empty or false) on the product.
func checkRequiredFields(policy StageGatePolicy, product *portfolioProduct) []string {
	var missing []string
	for _, field := range policy.RequiredFields {
		switch field {
		case "icp":
			if strings.TrimSpace(product.ICP) == "" {
				missing = append(missing, "icp")
			}
		case "name":
			if strings.TrimSpace(product.Name) == "" {
				missing = append(missing, "name")
			}
		case "primary_metric":
			if strings.TrimSpace(product.PrimaryMetric) == "" {
				missing = append(missing, "primary_metric")
			}
		case "primary_risk":
			if strings.TrimSpace(product.PrimaryRisk) == "" {
				missing = append(missing, "primary_risk")
			}
		case "validation_hypothesis":
			if strings.TrimSpace(product.ValidationHypothesis) == "" {
				missing = append(missing, "validation_hypothesis")
			}
		case "deploy_ready":
			if !product.DeployReady {
				missing = append(missing, "deploy_ready (must be true)")
			}
		case "analytics_ready":
			if !product.AnalyticsReady {
				missing = append(missing, "analytics_ready (must be true)")
			}
		}
	}
	return missing
}

// StageGateResult is the outcome of a stage gate check.
type StageGateResult struct {
	// Allowed is true when the task may proceed.
	Allowed bool
	// Warning is a non-empty advisory message when the gate passes but there
	// are policy concerns (e.g. required fields missing but domain unknown).
	Warning string
	// BlockReason is non-empty when Allowed is false, explaining why.
	BlockReason string
	// ProductKey is the matched product key, or "" if unmatched.
	ProductKey string
	// Stage is the product's current stage, or "" if unmatched.
	Stage string
}

// EnforceStageGate checks whether taskType is allowed for a task in the given
// domain.  It returns a StageGateResult describing the outcome.
//
// Rules:
//  1. Infrastructure domains (forge, forged, openclaw, …) always pass.
//  2. If the domain maps to no known product, the gate passes (untracked work).
//  3. If the product's stage has no policy, the gate passes.
//  4. If required fields are missing the gate is advisory (Warning, not Block).
//  5. If the task type is not in AllowedTaskTypes, the gate blocks.
func EnforceStageGate(domain string, taskType string) StageGateResult {
	// Rule 1: infrastructure bypass.
	if infrastructureDomains[strings.ToLower(domain)] {
		return StageGateResult{Allowed: true}
	}

	state, err := loadPortfolioState()
	if err != nil {
		// If we can't load config, log and pass through rather than blocking.
		return StageGateResult{
			Allowed: true,
			Warning: fmt.Sprintf("stage gate skipped: could not load portfolio-state.yaml: %v", err),
		}
	}

	// Rule 2: untracked domain passes through.
	product := findProductByDomain(state, domain)
	if product == nil {
		return StageGateResult{Allowed: true}
	}

	stage := strings.ToLower(strings.TrimSpace(product.Stage))
	policy, hasPolicty := stageGatePolicies[stage]
	// Rule 3: unknown stage passes through.
	if !hasPolicty {
		return StageGateResult{
			Allowed:    true,
			ProductKey: product.Key,
			Stage:      stage,
		}
	}

	// Rule 4: check required fields (advisory only — returns Warning, not Block).
	missingFields := checkRequiredFields(policy, product)
	var warning string
	if len(missingFields) > 0 {
		warning = fmt.Sprintf(
			"stage gate advisory: product '%s' (stage: %s) is missing required fields: %s. "+
				"Update config/portfolio/portfolio-state.yaml to resolve.",
			product.Key, stage, strings.Join(missingFields, ", "),
		)
	}

	// Rule 5: check task type allowlist.
	if !taskTypeAllowed(policy, taskType) {
		return StageGateResult{
			Allowed:     false,
			ProductKey:  product.Key,
			Stage:       stage,
			BlockReason: fmt.Sprintf("%s (task_type='%s' not allowed in stage='%s'; allowed: %s)", policy.BlockMessage, taskType, stage, strings.Join(policy.AllowedTaskTypes, ", ")),
			Warning:     warning,
		}
	}

	return StageGateResult{
		Allowed:    true,
		ProductKey: product.Key,
		Stage:      stage,
		Warning:    warning,
	}
}

// EnforceStageGateByKey is like EnforceStageGate but looks up the product by
// its key rather than its domain. Useful when the task request carries an
// explicit product key.
func EnforceStageGateByKey(productKey string, taskType string) StageGateResult {
	state, err := loadPortfolioState()
	if err != nil {
		return StageGateResult{
			Allowed: true,
			Warning: fmt.Sprintf("stage gate skipped: could not load portfolio-state.yaml: %v", err),
		}
	}

	product := findProductByKey(state, productKey)
	if product == nil {
		return StageGateResult{Allowed: true}
	}

	// Delegate to the domain-based check using the product's own domain.
	return EnforceStageGate(product.Domain, taskType)
}
