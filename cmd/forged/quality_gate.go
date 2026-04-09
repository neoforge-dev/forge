//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"log"
	"regexp"
	"strconv"
	"strings"
)

// qualityGateBlock holds structured quality metrics from a result file's
// <!-- QUALITY_GATE {...} --> block. Fields map directly to quality_gate_results columns.
// files_changed is parsed but stored separately (not in quality_gate_results schema).
type qualityGateBlock struct {
	TestPassRate float64 `json:"test_pass_rate"`
	CoveragePct  float64 `json:"coverage_pct"`
	LintIssues   int     `json:"lint_issues"`
	FilesChanged int     `json:"files_changed"`
}

// reQualityGateBlock matches <!-- QUALITY_GATE\n{...}\n--> across multiple lines.
var reQualityGateBlock = regexp.MustCompile(`(?s)<!--\s*QUALITY_GATE\s*(\{.*?\})\s*-->`)

// parseQualityGateJSON extracts the structured quality gate block from result file
// content. Fleet agents embed this block as an HTML comment:
//
//	<!-- QUALITY_GATE
//	{"test_pass_rate": 0.95, "coverage_pct": 82.5, "lint_issues": 3, "files_changed": 5}
//	-->
//
// Returns (block, true) when a valid block is found and parsed, (zero, false) otherwise.
func parseQualityGateJSON(content string) (qualityGateBlock, bool) {
	m := reQualityGateBlock.FindStringSubmatch(content)
	if len(m) < 2 {
		return qualityGateBlock{}, false
	}
	raw := strings.TrimSpace(m[1])
	var b qualityGateBlock
	if err := json.Unmarshal([]byte(raw), &b); err != nil {
		log.Printf("[quality-gate] malformed QUALITY_GATE JSON: %v", err)
		return qualityGateBlock{}, false
	}
	// Clamp values to valid ranges — guard against agent mistakes.
	b.TestPassRate = clamp01(b.TestPassRate)
	if b.CoveragePct < 0 {
		b.CoveragePct = 0
	}
	if b.CoveragePct > 100 {
		b.CoveragePct = 100
	}
	if b.LintIssues < 0 {
		b.LintIssues = 0
	}
	if b.FilesChanged < 0 {
		b.FilesChanged = 0
	}
	return b, true
}

// insertQualityGateResults extracts quality metrics from a task result string
// and inserts them into quality_gate_results. Best-effort — errors are logged
// but never block task completion.
//
// Extraction strategy (in order of preference):
//  1. Structured <!-- QUALITY_GATE {...} --> JSON block — precise, agent-authored
//  2. Regex heuristics on freeform text — best-effort fallback
func insertQualityGateResults(ctx context.Context, db *sql.DB, taskID, result string) {
	if db == nil || taskID == "" {
		return
	}

	var testPassRate float64
	var coveragePct float64
	var lintIssues int

	if block, ok := parseQualityGateJSON(result); ok {
		testPassRate = block.TestPassRate
		coveragePct = block.CoveragePct
		lintIssues = block.LintIssues
	} else {
		testPassRate = extractTestPassRate(result)
		coveragePct = extractCoveragePct(result)
		lintIssues = extractLintIssues(result)
	}

	_, err := db.ExecContext(ctx, `
		INSERT INTO quality_gate_results (task_id, test_pass_rate, coverage_pct, lint_issues)
		VALUES (?, ?, ?, ?)
	`, taskID, testPassRate, coveragePct, lintIssues)
	if err != nil {
		log.Printf("[quality-gate] insert failed for task %s: %v", taskID, err)
	}
}

// reXoverY matches "X/Y" pass patterns like "42/42 PASS" or "Tests: 40/42".
var reXoverY = regexp.MustCompile(`(?i)(\d+)\s*/\s*(\d+)\s*(?:pass(?:ed)?)?`)

// rePctPass matches "100% pass" or "passed 100%" patterns.
var rePctPass = regexp.MustCompile(`(?i)(\d+(?:\.\d+)?)\s*%\s*pass(?:ed)?|pass(?:ed)?\s+(\d+(?:\.\d+)?)\s*%`)

// reAllPass matches phrases meaning all tests passed with no count given.
var reAllPass = regexp.MustCompile(`(?i)\ball\s+tests?\s+pass(?:ed)?\b|tests?\s+pass(?:ed)?\b|\bPASS\b`)

// reFail matches explicit failure indicators.
var reFail = regexp.MustCompile(`(?i)\bFAIL(?:ED)?\b|\btest.*fail|fail.*test`)

// extractTestPassRate looks for pass-rate patterns in freeform agent result text.
// Returns a value in [0.0, 1.0].  Defaults to 0.0 when nothing is found.
func extractTestPassRate(result string) float64 {
	if result == "" {
		return 0.0
	}

	// "X/Y PASS" or "Tests: X/Y" — most specific pattern, try first.
	if m := reXoverY.FindStringSubmatch(result); len(m) == 3 {
		passed, err1 := strconv.ParseFloat(m[1], 64)
		total, err2 := strconv.ParseFloat(m[2], 64)
		if err1 == nil && err2 == nil && total > 0 {
			rate := passed / total
			if rate > 1.0 {
				rate = 1.0
			}
			return rate
		}
	}

	// "X% pass" or "pass X%".
	if m := rePctPass.FindStringSubmatch(result); len(m) >= 2 {
		raw := m[1]
		if raw == "" && len(m) > 2 {
			raw = m[2]
		}
		if pct, err := strconv.ParseFloat(raw, 64); err == nil {
			return clamp01(pct / 100.0)
		}
	}

	// Explicit FAIL — return 0.0 before checking for generic PASS.
	if reFail.MatchString(result) {
		return 0.0
	}

	// Generic "all tests pass" / "PASS" / "tests pass".
	if reAllPass.MatchString(result) {
		return 1.0
	}

	return 0.0
}

// reCoverageLine matches "coverage: 85.4%" or "85% coverage" patterns.
var reCoverageLine = regexp.MustCompile(`(?i)coverage[:\s]+(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*%\s*coverage`)

// extractCoveragePct looks for coverage percentage values in freeform text.
// Returns a value in [0.0, 100.0]. Defaults to 0.0 when nothing is found.
func extractCoveragePct(result string) float64 {
	if result == "" {
		return 0.0
	}
	if m := reCoverageLine.FindStringSubmatch(result); len(m) >= 3 {
		raw := m[1]
		if raw == "" {
			raw = m[2]
		}
		if pct, err := strconv.ParseFloat(raw, 64); err == nil {
			if pct > 100.0 {
				pct = 100.0
			}
			return pct
		}
	}
	return 0.0
}

// reLintCount matches "3 lint issues", "ruff: 3 errors", "golangci-lint: 5 issues".
var reLintCount = regexp.MustCompile(`(?i)(\d+)\s+(?:lint\s+(?:issues?|errors?|warnings?)|errors?|warnings?|issues?)`)

// reLintClean matches "lint: clean", "0 lint issues", "no lint issues".
var reLintClean = regexp.MustCompile(`(?i)lint[:\s]+clean\b|no\s+lint\s+(?:issues?|errors?|warnings?)\b|lint\s+pass(?:ed)?\b`)

// reLintToolCount matches "ruff: 3 errors", "golangci-lint: 5 issues", etc.
var reLintToolCount = regexp.MustCompile(`(?i)(?:ruff|golangci-lint|eslint|pylint|flake8)[:\s]+(\d+)\s+(?:errors?|warnings?|issues?)`)

// extractLintIssues looks for lint issue counts in freeform text.
// Returns 0 when the result is clean or unspecified.
func extractLintIssues(result string) int {
	if result == "" {
		return 0
	}
	// "lint: clean" / "no lint issues" — explicit zero, check before numeric pattern.
	if reLintClean.MatchString(result) {
		return 0
	}
	// Look for a numeric count.
	if m := reLintCount.FindStringSubmatch(result); len(m) >= 2 {
		n, err := strconv.Atoi(m[1])
		if err == nil {
			if n < 0 {
				return 0
			}
			return n
		}
	}
	// Also match "ruff: N errors" pattern where the tool name comes before the count.
	if m := reLintToolCount.FindStringSubmatch(result); len(m) >= 2 {
		n, err := strconv.Atoi(m[1])
		if err == nil && n >= 0 {
			return n
		}
	}
	return 0
}

// clamp01 ensures v is in [0.0, 1.0].
func clamp01(v float64) float64 {
	if v < 0.0 {
		return 0.0
	}
	if v > 1.0 {
		return 1.0
	}
	return v
}
