//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"fmt"
	"os"
	"time"

	"gopkg.in/yaml.v3"
)

type LaneConfig struct {
	Description string   `yaml:"description"`
	Gates       []string `yaml:"gates"`
	Thresholds  struct {
		Coverage      float64 `yaml:"coverage"`
		MaxLintIssues int     `yaml:"max_lint_issues"`
	} `yaml:"thresholds"`
	AutoProgress     bool          `yaml:"auto_progress"`
	RequiresApproval bool          `yaml:"requires_approval"`
	Timeout          time.Duration `yaml:"timeout"`
}

type RootLaneConfig struct {
	Lanes map[string]LaneConfig `yaml:"lanes"`
}

func LoadLaneConfig(path string) (map[Lane]LaneConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read lane config: %w", err)
	}

	var root RootLaneConfig
	if err := yaml.Unmarshal(data, &root); err != nil {
		return nil, fmt.Errorf("unmarshal lane config: %w", err)
	}

	config := make(map[Lane]LaneConfig)
	for name, cfg := range root.Lanes {
		config[Lane(name)] = cfg
	}

	return config, nil
}
