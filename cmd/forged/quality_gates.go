// cmd/forged/quality_gates.go
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

type QualityGateChecker struct {
	config QualityGatesConfig
}

type QualityGatesConfig struct {
	Coverage struct {
		Enabled   bool    `yaml:"enabled"`
		Threshold float64 `yaml:"threshold"`
		Command   string  `yaml:"command"`
	} `yaml:"coverage"`
	Lint struct {
		Enabled   bool   `yaml:"enabled"`
		MaxIssues int    `yaml:"max_issues"`
		Command   string `yaml:"command"`
	} `yaml:"lint"`
	TypeCheck struct {
		Enabled bool   `yaml:"enabled"`
		Command string `yaml:"command"`
	} `yaml:"type_check"`
}

func NewQualityGateChecker(configPath string) (*QualityGateChecker, error) {
	// Default configuration
	config := QualityGatesConfig{
		Coverage: struct {
			Enabled   bool    `yaml:"enabled"`
			Threshold float64 `yaml:"threshold"`
			Command   string  `yaml:"command"`
		}{
			Enabled:   true,
			Threshold: 80.0,
			Command:   "go test -cover ./...",
		},
		Lint: struct {
			Enabled   bool   `yaml:"enabled"`
			MaxIssues int    `yaml:"max_issues"`
			Command   string `yaml:"command"`
		}{
			Enabled:   true,
			MaxIssues: 0,
			Command:   "golangci-lint run --out-format json",
		},
		TypeCheck: struct {
			Enabled bool   `yaml:"enabled"`
			Command string `yaml:"command"`
		}{
			Enabled: true,
			Command: "go build ./...",
		},
	}

	// Load from YAML if configPath provided and file exists
	if configPath != "" {
		data, err := os.ReadFile(configPath)
		if err == nil {
			var fileConfig QualityGatesConfig
			if yaml.Unmarshal(data, &fileConfig) == nil {
				if fileConfig.Coverage.Threshold > 0 {
					config.Coverage.Threshold = fileConfig.Coverage.Threshold
				}
				if fileConfig.Coverage.Command != "" {
					config.Coverage.Command = fileConfig.Coverage.Command
				}
				config.Coverage.Enabled = fileConfig.Coverage.Enabled
				if fileConfig.Lint.Command != "" {
					config.Lint.Command = fileConfig.Lint.Command
				}
				config.Lint.MaxIssues = fileConfig.Lint.MaxIssues
				config.Lint.Enabled = fileConfig.Lint.Enabled
				if fileConfig.TypeCheck.Command != "" {
					config.TypeCheck.Command = fileConfig.TypeCheck.Command
				}
				config.TypeCheck.Enabled = fileConfig.TypeCheck.Enabled
			}
		}
	}

	// Override with environment variables if present (useful for tests)
	if cmd := os.Getenv("FORGE_TEST_COVERAGE_CMD"); cmd != "" {
		config.Coverage.Command = cmd
	}
	if cmd := os.Getenv("FORGE_TEST_LINT_CMD"); cmd != "" {
		config.Lint.Command = cmd
	}
	if cmd := os.Getenv("FORGE_TEST_TYPECHECK_CMD"); cmd != "" {
		config.TypeCheck.Command = cmd
	}

	return &QualityGateChecker{
		config: config,
	}, nil
}

func (q *QualityGateChecker) CheckCoverage(ctx context.Context) (float64, error) {
	cmd := exec.CommandContext(ctx, "sh", "-c", q.config.Coverage.Command)
	output, err := cmd.CombinedOutput()
	if err != nil && len(output) == 0 {
		return 0, err
	}

	// Parse total coverage from go test -cover output
	lines := strings.Split(string(output), "\n")
	var totalCoverage float64
	var count int

	coverageRegex := regexp.MustCompile(`coverage:\s*([\d.]+)%`)

	for _, line := range lines {
		if strings.Contains(line, "coverage:") {
			matches := coverageRegex.FindStringSubmatch(line)
			if len(matches) > 1 {
				cov, _ := strconv.ParseFloat(matches[1], 64)
				totalCoverage += cov
				count++
			}
		}
	}

	if count > 0 {
		return totalCoverage / float64(count), nil
	}
	return 0, nil
}

func (q *QualityGateChecker) CheckLint(ctx context.Context) ([]LintIssue, error) {
	cmd := exec.CommandContext(ctx, "sh", "-c", q.config.Lint.Command)
	output, err := cmd.CombinedOutput()
	if err != nil && len(output) == 0 {
		return nil, err
	}

	// Parse golangci-lint JSON output
	var issues []LintIssue
	var result struct {
		Issues []struct {
			Text string `json:"Text"`
			Pos  struct {
				Filename string `json:"Filename"`
				Line     int    `json:"Line"`
			} `json:"Pos"`
			FromLinter string `json:"FromLinter"`
		} `json:"Issues"`
	}

	if err := json.Unmarshal(output, &result); err == nil {
		for _, issue := range result.Issues {
			issues = append(issues, LintIssue{
				File:    issue.Pos.Filename,
				Line:    issue.Pos.Line,
				Message: issue.Text,
				Linter:  issue.FromLinter,
			})
		}
	}

	return issues, nil
}

type LintIssue struct {
	File    string
	Line    int
	Message string
	Linter  string
}

func (q *QualityGateChecker) CheckTypeCheck(ctx context.Context) error {
	cmd := exec.CommandContext(ctx, "sh", "-c", q.config.TypeCheck.Command)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("type check failed: %s", string(output))
	}
	return nil
}
