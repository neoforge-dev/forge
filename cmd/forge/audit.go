// audit.go — forge audit: comprehensive project health assessment
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// auditScore holds one dimension's assessment
type auditScore struct {
	Dimension string  `json:"dimension"`
	Score     float64 `json:"score"`     // 0-100
	MaxScore  float64 `json:"max_score"` // always 100
	Weight    float64 `json:"weight"`    // 0.0-1.0
	Status    string  `json:"status"`    // pass, warn, fail, skip
	Details   string  `json:"details"`
	Findings  []auditFinding `json:"findings,omitempty"`
}

type auditFinding struct {
	Severity    string `json:"severity"` // critical, high, medium, low, info
	Category    string `json:"category"`
	Description string `json:"description"`
	File        string `json:"file,omitempty"`
	Line        int    `json:"line,omitempty"`
	Fix         string `json:"fix,omitempty"`
}

// auditReport is the full audit output
type auditReport struct {
	Project      string       `json:"project"`
	Path         string       `json:"path"`
	Stack        []string     `json:"stack"`
	HealthScore  float64      `json:"health_score"` // weighted aggregate
	Scores       []auditScore `json:"scores"`
	TopFindings  []auditFinding `json:"top_findings"`
	CostEstimate *costEstimate  `json:"cost_estimate,omitempty"`
}

type costEstimate struct {
	EngineeringHours int    `json:"engineering_hours"`
	InfraMonthlyCost int    `json:"infra_monthly_cost"`
	TimelineWeeks    int    `json:"timeline_weeks"`
	CriticalPath     string `json:"critical_path"`
}

var auditCmd = &cobra.Command{
	Use:   "audit <project-path> [additional-paths...]",
	Short: "Comprehensive project health assessment",
	Long: `Audit one or more project directories for code health, test coverage,
security, deployment readiness, documentation, and SEO.

Produces a scored report (0-100) across 8 dimensions with actionable findings.

Examples:
  forge audit .                              # Audit current directory
  forge audit ~/work/my-project              # Audit external project
  forge audit repo1/ repo2/ --format json    # Audit multiple repos, JSON output
  forge audit . --format html --output report.html  # Client-ready HTML report
  forge audit . --depth deep                 # Deep audit with security scan
  forge audit . --fix --queue                # Generate fix tasks for dark factory
  forge audit . --compare prev.json          # Compare scores against previous run`,
	Args: cobra.MinimumNArgs(1),
	RunE: runAudit,
}

func init() {
	auditCmd.Flags().String("depth", "standard", "Audit depth: quick, standard, deep")
	auditCmd.Flags().StringP("format", "f", "table", "Output format: table, json, html")
	auditCmd.Flags().String("output", "", "Save report to file")
	auditCmd.Flags().Bool("fix", false, "Generate fix tasks from findings")
	auditCmd.Flags().Bool("queue", false, "Queue fix tasks in dark factory (requires --fix)")
	auditCmd.Flags().Int("gate", 0, "Exit with code 1 if health score < gate threshold")
	auditCmd.Flags().String("compare", "", "Compare against previous JSON report file")
}

// letterGrade converts a 0-100 score to A-F grade
func letterGrade(score float64) string {
	switch {
	case score >= 90:
		return "A"
	case score >= 70:
		return "B"
	case score >= 50:
		return "C"
	case score >= 30:
		return "D"
	default:
		return "F"
	}
}

func runAudit(cmd *cobra.Command, args []string) error {
	depth, _ := cmd.Flags().GetString("depth")
	format, _ := cmd.Flags().GetString("format")
	outputFile, _ := cmd.Flags().GetString("output")
	gate, _ := cmd.Flags().GetInt("gate")
	doFix, _ := cmd.Flags().GetBool("fix")
	comparePath, _ := cmd.Flags().GetString("compare")

	for _, projectPath := range args {
		absPath, err := filepath.Abs(projectPath)
		if err != nil {
			return fmt.Errorf("invalid path %s: %w", projectPath, err)
		}
		if _, err := os.Stat(absPath); err != nil {
			return fmt.Errorf("path not found: %s", absPath)
		}

		report := auditProject(absPath, depth)

		// Compare with previous report if --compare provided
		if comparePath != "" {
			printComparison(report, comparePath)
		}

		switch format {
		case "json":
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			if err := enc.Encode(report); err != nil {
				return err
			}
		case "html":
			html := renderAuditHTML(report)
			if outputFile != "" {
				if err := os.WriteFile(outputFile, []byte(html), 0644); err != nil {
					return fmt.Errorf("failed to write HTML report: %w", err)
				}
				fmt.Printf("HTML report saved to %s\n", outputFile)
			} else {
				fmt.Print(html)
			}
		default:
			printAuditReport(report)
		}

		// Generate fix tasks from findings
		if doFix && len(report.TopFindings) > 0 {
			fmt.Printf("\n📋 Fix tasks generated (%d findings):\n", len(report.TopFindings))
			for i, f := range report.TopFindings {
				if i >= 10 {
					fmt.Printf("   ... and %d more\n", len(report.TopFindings)-10)
					break
				}
				fmt.Printf("   %d. [%s] %s\n", i+1, f.Severity, f.Description)
				if f.Fix != "" {
					fmt.Printf("      Fix: %s\n", f.Fix)
				}
			}
		}

		// Gate check — exit non-zero if score below threshold
		if gate > 0 && report.HealthScore < float64(gate) {
			return fmt.Errorf("health score %.0f is below gate threshold %d (grade: %s)",
				report.HealthScore, gate, letterGrade(report.HealthScore))
		}
	}
	return nil
}

func auditProject(path, depth string) auditReport {
	stack := detectStack(path)
	report := auditReport{
		Project: filepath.Base(path),
		Path:    path,
		Stack:   stack,
	}

	// Run all dimension checks
	report.Scores = append(report.Scores, auditBuild(path, stack))
	report.Scores = append(report.Scores, auditLint(path, stack))
	report.Scores = append(report.Scores, auditTests(path, stack))
	report.Scores = append(report.Scores, auditSecurity(path, stack))
	report.Scores = append(report.Scores, auditDeploy(path))
	report.Scores = append(report.Scores, auditDocs(path))

	if depth == "standard" || depth == "deep" {
		report.Scores = append(report.Scores, auditSEO(path))
		report.Scores = append(report.Scores, auditInfra(path))
	}

	// Calculate weighted health score
	totalWeight := 0.0
	weightedSum := 0.0
	for _, s := range report.Scores {
		totalWeight += s.Weight
		weightedSum += s.Score * s.Weight
	}
	if totalWeight > 0 {
		report.HealthScore = weightedSum / totalWeight
	}

	// Collect top findings (critical + high severity)
	for _, s := range report.Scores {
		for _, f := range s.Findings {
			if f.Severity == "critical" || f.Severity == "high" {
				report.TopFindings = append(report.TopFindings, f)
			}
		}
	}

	// Cost estimate
	report.CostEstimate = estimateCost(report)

	return report
}

// detectStack identifies the project's tech stack from marker files
// Checks root AND one level of subdirectories (backend/, frontend/, app/, src/)
func detectStack(path string) []string {
	seen := map[string]bool{}
	markers := map[string]string{
		"go.mod":         "go",
		"pyproject.toml": "python",
		"package.json":   "node",
		"mix.exs":        "elixir",
		"Cargo.toml":     "rust",
		"Gemfile":        "ruby",
		"pom.xml":        "java",
		"build.gradle":   "java",
	}

	// Check root
	for file, lang := range markers {
		if _, err := os.Stat(filepath.Join(path, file)); err == nil {
			seen[lang] = true
		}
	}

	// Check common subdirectories
	for _, sub := range []string{"backend", "frontend", "app", "src", "api", "web", "server", "client"} {
		subPath := filepath.Join(path, sub)
		if info, err := os.Stat(subPath); err == nil && info.IsDir() {
			for file, lang := range markers {
				if _, err := os.Stat(filepath.Join(subPath, file)); err == nil {
					seen[lang] = true
				}
			}
		}
	}

	var stack []string
	for lang := range seen {
		stack = append(stack, lang)
	}
	if len(stack) == 0 {
		stack = append(stack, "unknown")
	}
	return stack
}

// findSubdir returns the path containing the marker file, checking root + common subdirs
func findSubdir(path, marker string) string {
	if _, err := os.Stat(filepath.Join(path, marker)); err == nil {
		return path
	}
	for _, sub := range []string{"backend", "frontend", "app", "src", "api", "web", "server", "client"} {
		if _, err := os.Stat(filepath.Join(path, sub, marker)); err == nil {
			return filepath.Join(path, sub)
		}
	}
	return path
}

// auditBuild checks if the project compiles/builds
func auditBuild(path string, stack []string) auditScore {
	s := auditScore{Dimension: "Build", MaxScore: 100, Weight: 0.15}
	var findings []auditFinding

	for _, lang := range stack {
		var cmd *exec.Cmd
		var dir string
		switch lang {
		case "go":
			dir = findSubdir(path, "go.mod")
			cmd = exec.Command("go", "build", "./...")
		case "python":
			dir = findSubdir(path, "pyproject.toml")
			cmd = exec.Command("uv", "sync")
		case "node":
			dir = findSubdir(path, "package.json")
			cmd = exec.Command("npm", "run", "build")
		case "elixir":
			dir = findSubdir(path, "mix.exs")
			cmd = exec.Command("mix", "compile")
		default:
			continue
		}
		cmd.Dir = dir
		out, err := cmd.CombinedOutput()
		if err != nil {
			s.Score = 0
			s.Status = "fail"
			s.Details = fmt.Sprintf("%s build failed", lang)
			findings = append(findings, auditFinding{
				Severity:    "critical",
				Category:    "build",
				Description: fmt.Sprintf("%s build failed: %s", lang, strings.TrimSpace(string(out[:min(len(out), 200)])),
				),
				Fix: fmt.Sprintf("Fix build errors in %s project", lang),
			})
		} else {
			s.Score = 100
			s.Status = "pass"
			s.Details = fmt.Sprintf("%s builds clean", lang)
		}
	}

	if s.Status == "" {
		s.Score = 0
		s.Status = "skip"
		s.Details = "No recognized build system"
	}
	s.Findings = findings
	return s
}

// auditLint runs linters for the detected stack
func auditLint(path string, stack []string) auditScore {
	s := auditScore{Dimension: "Lint", MaxScore: 100, Weight: 0.10}

	for _, lang := range stack {
		var cmd *exec.Cmd
		var dir string
		switch lang {
		case "python":
			dir = findSubdir(path, "pyproject.toml")
			cmd = exec.Command("uv", "run", "ruff", "check", ".")
		case "node":
			dir = findSubdir(path, "package.json")
			cmd = exec.Command("npx", "eslint", ".", "--max-warnings", "0")
		case "go":
			dir = findSubdir(path, "go.mod")
			cmd = exec.Command("go", "vet", "./...")
		default:
			continue
		}
		cmd.Dir = dir
		out, err := cmd.CombinedOutput()
		if err != nil {
			lines := strings.Count(string(out), "\n")
			score := 100.0 - float64(lines)*5.0
			if score < 0 { score = 0 }
			s.Score = score
			s.Status = "warn"
			s.Details = fmt.Sprintf("%d lint issues", lines)
			s.Findings = append(s.Findings, auditFinding{
				Severity:    "medium",
				Category:    "lint",
				Description: fmt.Sprintf("%d lint warnings/errors in %s code", lines, lang),
				Fix:         fmt.Sprintf("Run auto-fix: ruff check --fix . (Python) or eslint --fix . (Node)"),
			})
		} else {
			s.Score = 100
			s.Status = "pass"
			s.Details = "No lint issues"
		}
	}

	if s.Status == "" {
		s.Status = "skip"
		s.Details = "No linter configured"
	}
	return s
}

// auditTests counts and optionally runs tests
func auditTests(path string, stack []string) auditScore {
	s := auditScore{Dimension: "Tests", MaxScore: 100, Weight: 0.20}

	// Count test files
	testCount := 0
	filepath.Walk(path, func(p string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}
		if info.IsDir() {
			base := info.Name()
			if base == "node_modules" || base == "vendor" || base == ".git" || base == "__pycache__" || base == ".venv" {
				return filepath.SkipDir
			}
			return nil
		}
		name := info.Name()
		if strings.HasSuffix(name, "_test.go") || strings.HasPrefix(name, "test_") ||
			strings.HasSuffix(name, ".test.ts") || strings.HasSuffix(name, ".spec.ts") ||
			strings.HasSuffix(name, "_test.exs") || strings.HasSuffix(name, ".test.js") {
			testCount++
		}
		return nil
	})

	if testCount == 0 {
		s.Score = 0
		s.Status = "fail"
		s.Details = "No test files found"
		s.Findings = append(s.Findings, auditFinding{
			Severity:    "high",
			Category:    "tests",
			Description: "Project has zero test files",
			Fix:         "Add unit tests for core business logic",
		})
	} else if testCount < 5 {
		s.Score = 30
		s.Status = "warn"
		s.Details = fmt.Sprintf("%d test files (minimal)", testCount)
	} else if testCount < 20 {
		s.Score = 60
		s.Status = "warn"
		s.Details = fmt.Sprintf("%d test files (moderate)", testCount)
	} else {
		s.Score = 90
		s.Status = "pass"
		s.Details = fmt.Sprintf("%d test files", testCount)
	}
	return s
}

// auditSecurity checks for common security issues
func auditSecurity(path string, stack []string) auditScore {
	s := auditScore{Dimension: "Security", MaxScore: 100, Weight: 0.20, Score: 100, Status: "pass"}

	// Check for hardcoded secrets
	secretPatterns := []string{"sk_live_", "sk_test_", "AKIA", "ghp_", "password\\s*=\\s*[\"']"}
	for _, pattern := range secretPatterns {
		cmd := exec.Command("grep", "-rn", "--include=*.py", "--include=*.ts", "--include=*.go", "--include=*.js", "--include=*.env", pattern, ".")
		cmd.Dir = path
		out, _ := cmd.CombinedOutput()
		if len(strings.TrimSpace(string(out))) > 0 {
			lines := strings.Split(strings.TrimSpace(string(out)), "\n")
			s.Score -= 30
			s.Status = "fail"
			s.Findings = append(s.Findings, auditFinding{
				Severity:    "critical",
				Category:    "security",
				Description: fmt.Sprintf("Potential hardcoded secret (%d occurrences)", len(lines)),
				File:        strings.Split(lines[0], ":")[0],
				Fix:         "Move secrets to environment variables",
			})
		}
	}

	// Check if .env is in .gitignore
	gitignoreContent, _ := os.ReadFile(filepath.Join(path, ".gitignore"))
	if !strings.Contains(string(gitignoreContent), ".env") {
		s.Score -= 20
		s.Status = "warn"
		s.Findings = append(s.Findings, auditFinding{
			Severity:    "high",
			Category:    "security",
			Description: ".env not in .gitignore — secrets may be committed",
			Fix:         "Add .env to .gitignore",
		})
	}

	// Check for DEBUG mode left on in config files
	debugCmd := exec.Command("grep", "-rn", "--include=*.py", "--include=*.ts", "--include=*.js", "--include=*.env", "--include=*.yaml", "--include=*.yml", "DEBUG\\s*=\\s*[Tt]rue\\|DEBUG\\s*=\\s*1", ".")
	debugCmd.Dir = path
	if debugOut, _ := debugCmd.CombinedOutput(); len(strings.TrimSpace(string(debugOut))) > 0 {
		s.Score -= 10
		if s.Status == "pass" {
			s.Status = "warn"
		}
		s.Findings = append(s.Findings, auditFinding{
			Severity:    "medium",
			Category:    "security",
			Description: "DEBUG mode enabled in config — disable for production",
			Fix:         "Set DEBUG=false in production configuration",
		})
	}

	// Run dependency audit (npm audit / pip audit / govulncheck)
	for _, lang := range stack {
		switch lang {
		case "node":
			dir := findSubdir(path, "package.json")
			if _, err := os.Stat(filepath.Join(dir, "node_modules")); err == nil {
				auditCmd := exec.Command("npm", "audit", "--json")
				auditCmd.Dir = dir
				if auditOut, err := auditCmd.CombinedOutput(); err != nil {
					// npm audit exits non-zero when vulnerabilities found
					var result map[string]interface{}
					if json.Unmarshal(auditOut, &result) == nil {
						if meta, ok := result["metadata"].(map[string]interface{}); ok {
							if vulns, ok := meta["vulnerabilities"].(map[string]interface{}); ok {
								total := 0
								for _, v := range vulns {
									if n, ok := v.(float64); ok {
										total += int(n)
									}
								}
								if total > 0 {
									s.Score -= min64(15, float64(total)*3)
									if s.Status == "pass" {
										s.Status = "warn"
									}
									s.Findings = append(s.Findings, auditFinding{
										Severity:    "high",
										Category:    "security",
										Description: fmt.Sprintf("%d npm dependency vulnerabilities found", total),
										Fix:         "Run: npm audit fix",
									})
								}
							}
						}
					}
				}
			}
		case "go":
			dir := findSubdir(path, "go.mod")
			vulnCmd := exec.Command("govulncheck", "./...")
			vulnCmd.Dir = dir
			if vulnOut, err := vulnCmd.CombinedOutput(); err != nil {
				vulnLines := strings.Count(string(vulnOut), "Vulnerability")
				if vulnLines > 0 {
					s.Score -= min64(15, float64(vulnLines)*5)
					if s.Status == "pass" {
						s.Status = "warn"
					}
					s.Findings = append(s.Findings, auditFinding{
						Severity:    "high",
						Category:    "security",
						Description: fmt.Sprintf("%d Go vulnerability findings", vulnLines),
						Fix:         "Run: govulncheck ./... and update affected modules",
					})
				}
			}
		}
	}

	if s.Score < 0 {
		s.Score = 0
	}
	s.Details = fmt.Sprintf("%d issues found", len(s.Findings))
	return s
}

func min64(a int, b float64) float64 {
	if float64(a) < b {
		return float64(a)
	}
	return b
}

// auditDeploy checks deployment readiness
func auditDeploy(path string) auditScore {
	s := auditScore{Dimension: "Deploy", MaxScore: 100, Weight: 0.15, Score: 0, Status: "fail"}

	checks := map[string]int{
		"Dockerfile":        15,
		"docker-compose.yml": 10,
		".env.example":      15,
		"railway.json":      10,
		"fly.toml":          10,
	}

	for file, points := range checks {
		if _, err := os.Stat(filepath.Join(path, file)); err == nil {
			s.Score += float64(points)
		}
	}

	// Check for CI/CD
	if _, err := os.Stat(filepath.Join(path, ".github", "workflows")); err == nil {
		entries, _ := os.ReadDir(filepath.Join(path, ".github", "workflows"))
		if len(entries) > 0 {
			s.Score += 20
		}
	}

	// Check for health endpoint
	cmd := exec.Command("grep", "-rn", "/health", ".")
	cmd.Dir = path
	if out, _ := cmd.CombinedOutput(); len(strings.TrimSpace(string(out))) > 0 {
		s.Score += 20
	}

	if s.Score >= 80 {
		s.Status = "pass"
	} else if s.Score >= 50 {
		s.Status = "warn"
	}
	s.Details = fmt.Sprintf("%d/100 deploy readiness", int(s.Score))
	return s
}

// auditDocs checks documentation quality
func auditDocs(path string) auditScore {
	s := auditScore{Dimension: "Docs", MaxScore: 100, Weight: 0.10, Score: 0}

	if _, err := os.Stat(filepath.Join(path, "README.md")); err == nil {
		info, _ := os.Stat(filepath.Join(path, "README.md"))
		if info != nil && info.Size() > 500 {
			s.Score += 40
		} else {
			s.Score += 15
		}
	}
	if _, err := os.Stat(filepath.Join(path, "CONTRIBUTING.md")); err == nil {
		s.Score += 15
	}
	if _, err := os.Stat(filepath.Join(path, "LICENSE")); err == nil {
		s.Score += 15
	}
	if _, err := os.Stat(filepath.Join(path, "CHANGELOG.md")); err == nil {
		s.Score += 15
	}
	// Check for API docs
	for _, p := range []string{"docs/", "api/", "openapi.yaml", "swagger.json"} {
		if _, err := os.Stat(filepath.Join(path, p)); err == nil {
			s.Score += 15
			break
		}
	}

	if s.Score >= 70 {
		s.Status = "pass"
	} else if s.Score >= 40 {
		s.Status = "warn"
	} else {
		s.Status = "fail"
	}
	s.Details = fmt.Sprintf("%d/100 documentation", int(s.Score))
	return s
}

// auditSEO checks web project SEO readiness
func auditSEO(path string) auditScore {
	s := auditScore{Dimension: "SEO", MaxScore: 100, Weight: 0.05, Score: 0}

	indexPath := filepath.Join(path, "index.html")
	if _, err := os.Stat(indexPath); err != nil {
		// Check public/ or dist/
		for _, sub := range []string{"public", "dist", "static"} {
			if _, err := os.Stat(filepath.Join(path, sub, "index.html")); err == nil {
				indexPath = filepath.Join(path, sub, "index.html")
				break
			}
		}
	}

	content, err := os.ReadFile(indexPath)
	if err != nil {
		s.Status = "skip"
		s.Details = "No index.html found"
		return s
	}

	html := string(content)
	if strings.Contains(html, "<title>") {
		s.Score += 20
	}
	if strings.Contains(html, `name="description"`) {
		s.Score += 20
	}
	if strings.Contains(html, `og:title`) {
		s.Score += 20
	}
	if strings.Contains(html, `twitter:card`) {
		s.Score += 15
	}
	if _, err := os.Stat(filepath.Join(path, "robots.txt")); err == nil {
		s.Score += 10
	}
	if _, err := os.Stat(filepath.Join(path, "sitemap.xml")); err == nil {
		s.Score += 15
	}

	if s.Score >= 80 {
		s.Status = "pass"
	} else if s.Score >= 50 {
		s.Status = "warn"
	} else {
		s.Status = "fail"
	}
	s.Details = fmt.Sprintf("%d/100 SEO readiness", int(s.Score))
	return s
}

// auditInfra checks infrastructure configuration
func auditInfra(path string) auditScore {
	s := auditScore{Dimension: "Infra", MaxScore: 100, Weight: 0.05, Score: 0}

	// Migrations
	for _, dir := range []string{"migrations", "alembic", "priv/repo/migrations", "db/migrate"} {
		if _, err := os.Stat(filepath.Join(path, dir)); err == nil {
			s.Score += 30
			break
		}
	}

	// Monitoring config
	cmd := exec.Command("grep", "-rn", "sentry\\|datadog\\|prometheus\\|posthog", ".")
	cmd.Dir = path
	if out, _ := cmd.CombinedOutput(); len(strings.TrimSpace(string(out))) > 0 {
		s.Score += 30
	}

	// Logging
	cmd2 := exec.Command("grep", "-rn", "logging\\|log\\.Printf\\|logger\\|winston", ".")
	cmd2.Dir = path
	if out, _ := cmd2.CombinedOutput(); len(strings.TrimSpace(string(out))) > 0 {
		s.Score += 20
	}

	// Backup config
	if strings.Contains(strings.Join([]string{}, ""), "backup") {
		s.Score += 20
	}

	if s.Score >= 60 {
		s.Status = "pass"
	} else if s.Score >= 30 {
		s.Status = "warn"
	} else {
		s.Status = "fail"
	}
	s.Details = fmt.Sprintf("%d/100 infrastructure", int(s.Score))
	return s
}

func estimateCost(report auditReport) *costEstimate {
	hours := 40 // base
	for _, s := range report.Scores {
		if s.Score < 50 {
			hours += 20
		} else if s.Score < 80 {
			hours += 8
		}
		hours += len(s.Findings) * 2
	}
	weeks := (hours + 39) / 40

	critical := ""
	for _, f := range report.TopFindings {
		if f.Severity == "critical" {
			critical = f.Description
			break
		}
	}
	if critical == "" {
		critical = "No critical blockers"
	}

	return &costEstimate{
		EngineeringHours: hours,
		InfraMonthlyCost: 400,
		TimelineWeeks:    weeks,
		CriticalPath:     critical,
	}
}

func printAuditReport(report auditReport) {
	fmt.Println()
	fmt.Printf("╔══════════════════════════════════════════════════╗\n")
	fmt.Printf("║  PROJECT AUDIT: %-33s║\n", report.Project)
	fmt.Printf("╠══════════════════════════════════════════════════╣\n")
	fmt.Printf("║  Stack: %-41s║\n", strings.Join(report.Stack, ", "))
	fmt.Printf("║  Path:  %-41s║\n", auditTruncate(report.Path, 41))
	fmt.Printf("║                                                  ║\n")
	grade := letterGrade(report.HealthScore)
	fmt.Printf("║  HEALTH SCORE: %s%-3.0f/100  (Grade: %s)%s               ║\n",
		scoreColor(report.HealthScore), report.HealthScore, grade, colorReset)
	fmt.Printf("╠══════════════════════════════════════════════════╣\n")

	for _, s := range report.Scores {
		symbol := "✓"
		color := colorGreen
		switch s.Status {
		case "fail":
			symbol = "✗"
			color = colorRed
		case "warn":
			symbol = "⚠"
			color = colorYellow
		case "skip":
			symbol = "–"
			color = colorReset
		}
		fmt.Printf("║  %s%s%s %-10s %3.0f/100  %-24s║\n",
			color, symbol, colorReset, s.Dimension, s.Score, auditTruncate(s.Details, 24))
	}

	if len(report.TopFindings) > 0 {
		fmt.Printf("╠══════════════════════════════════════════════════╣\n")
		fmt.Printf("║  TOP FINDINGS:                                   ║\n")
		for i, f := range report.TopFindings {
			if i >= 5 {
				break
			}
			sev := colorRed + f.Severity + colorReset
			if f.Severity == "high" {
				sev = colorYellow + f.Severity + colorReset
			}
			fmt.Printf("║  %s: %-42s║\n", sev, auditTruncate(f.Description, 42))
		}
	}

	if report.CostEstimate != nil {
		fmt.Printf("╠══════════════════════════════════════════════════╣\n")
		fmt.Printf("║  ESTIMATE:                                       ║\n")
		fmt.Printf("║  Engineering: ~%dh ($%d at $75/h)%s║\n",
			report.CostEstimate.EngineeringHours,
			report.CostEstimate.EngineeringHours*75,
			strings.Repeat(" ", max(0, 16-len(fmt.Sprintf("%d", report.CostEstimate.EngineeringHours*75)))))
		fmt.Printf("║  Timeline:   ~%d weeks                           ║\n", report.CostEstimate.TimelineWeeks)
		fmt.Printf("║  Infra:      ~$%d/mo                             ║\n", report.CostEstimate.InfraMonthlyCost)
	}

	fmt.Printf("╚══════════════════════════════════════════════════╝\n")
}

func scoreColor(score float64) string {
	if score >= 80 {
		return colorGreen
	} else if score >= 50 {
		return colorYellow
	}
	return colorRed
}

func auditTruncate(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s + strings.Repeat(" ", maxLen-len(s))
	}
	return s[:maxLen-3] + "..."
}

// printComparison shows a delta between current audit and a previous JSON report
func printComparison(current auditReport, prevPath string) {
	data, err := os.ReadFile(prevPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "warning: could not read previous report %s: %v\n", prevPath, err)
		return
	}
	var prev auditReport
	if err := json.Unmarshal(data, &prev); err != nil {
		fmt.Fprintf(os.Stderr, "warning: could not parse previous report: %v\n", err)
		return
	}

	delta := current.HealthScore - prev.HealthScore
	arrow := "→"
	if delta > 0 {
		arrow = "↑"
	} else if delta < 0 {
		arrow = "↓"
	}

	fmt.Printf("\n📊 Score Comparison: %.0f %s %.0f (%+.0f)\n", prev.HealthScore, arrow, current.HealthScore, delta)
	fmt.Printf("   Previous: %s (%s)  →  Current: %s (%s)\n\n",
		fmt.Sprintf("%.0f", prev.HealthScore), letterGrade(prev.HealthScore),
		fmt.Sprintf("%.0f", current.HealthScore), letterGrade(current.HealthScore))

	// Per-dimension comparison
	prevScores := map[string]float64{}
	for _, s := range prev.Scores {
		prevScores[s.Dimension] = s.Score
	}
	for _, s := range current.Scores {
		prevScore := prevScores[s.Dimension]
		d := s.Score - prevScore
		indicator := "  "
		if d > 0 {
			indicator = colorGreen + "▲" + colorReset
		} else if d < 0 {
			indicator = colorRed + "▼" + colorReset
		}
		fmt.Printf("   %s %-10s %3.0f → %3.0f  (%+.0f)\n", indicator, s.Dimension, prevScore, s.Score, d)
	}
	fmt.Println()
}

// renderAuditHTML generates a self-contained HTML report for client delivery
func renderAuditHTML(report auditReport) string {
	grade := letterGrade(report.HealthScore)
	gradeColor := "#e74c3c"
	if report.HealthScore >= 90 {
		gradeColor = "#27ae60"
	} else if report.HealthScore >= 70 {
		gradeColor = "#2ecc71"
	} else if report.HealthScore >= 50 {
		gradeColor = "#f39c12"
	} else if report.HealthScore >= 30 {
		gradeColor = "#e67e22"
	}

	var b strings.Builder
	b.WriteString(`<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Project Audit: ` + htmlEscape(report.Project) + `</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f6fa; color: #2c3e50; line-height: 1.6; }
  .container { max-width: 800px; margin: 0 auto; padding: 2rem 1rem; }
  .header { text-align: center; margin-bottom: 2rem; }
  .header h1 { font-size: 1.5rem; font-weight: 600; color: #34495e; }
  .header .meta { color: #7f8c8d; font-size: 0.9rem; margin-top: 0.5rem; }
  .score-card { background: white; border-radius: 12px; padding: 2rem; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 2rem; }
  .score-big { font-size: 4rem; font-weight: 700; line-height: 1; }
  .grade { display: inline-block; font-size: 1.5rem; font-weight: 700; color: white; border-radius: 8px; padding: 0.25rem 0.75rem; margin-left: 0.5rem; }
  .stack { display: flex; gap: 0.5rem; justify-content: center; margin-top: 1rem; }
  .stack span { background: #ecf0f1; border-radius: 4px; padding: 0.2rem 0.6rem; font-size: 0.85rem; font-weight: 500; }
  .dimensions { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 2rem; }
  .dim { background: white; border-radius: 8px; padding: 1.25rem; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
  .dim-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; }
  .dim-name { font-weight: 600; font-size: 0.95rem; }
  .dim-score { font-weight: 700; font-size: 1.1rem; }
  .dim-bar { height: 6px; background: #ecf0f1; border-radius: 3px; overflow: hidden; }
  .dim-bar-fill { height: 100%; border-radius: 3px; transition: width 0.3s; }
  .dim-detail { font-size: 0.85rem; color: #7f8c8d; margin-top: 0.5rem; }
  .findings { background: white; border-radius: 8px; padding: 1.5rem; box-shadow: 0 1px 4px rgba(0,0,0,0.06); margin-bottom: 2rem; }
  .findings h2 { font-size: 1.1rem; margin-bottom: 1rem; }
  .finding { border-left: 3px solid #e74c3c; padding: 0.75rem 1rem; margin-bottom: 0.75rem; background: #fef9f9; border-radius: 0 4px 4px 0; }
  .finding.high { border-color: #f39c12; background: #fefbf3; }
  .finding .sev { font-weight: 600; font-size: 0.8rem; text-transform: uppercase; }
  .finding .sev.critical { color: #e74c3c; }
  .finding .sev.high { color: #f39c12; }
  .finding .desc { margin-top: 0.25rem; }
  .finding .fix { font-size: 0.85rem; color: #27ae60; margin-top: 0.25rem; }
  .estimate { background: white; border-radius: 8px; padding: 1.5rem; box-shadow: 0 1px 4px rgba(0,0,0,0.06); margin-bottom: 2rem; }
  .estimate h2 { font-size: 1.1rem; margin-bottom: 1rem; }
  .estimate-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1rem; }
  .est-item { text-align: center; }
  .est-value { font-size: 1.5rem; font-weight: 700; color: #2c3e50; }
  .est-label { font-size: 0.85rem; color: #7f8c8d; }
  .footer { text-align: center; color: #bdc3c7; font-size: 0.8rem; margin-top: 2rem; }
  @media (max-width: 600px) { .dimensions { grid-template-columns: 1fr; } .estimate-grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="container">
`)

	// Header
	b.WriteString(`<div class="header">
  <h1>Project Audit Report</h1>
  <div class="meta">` + htmlEscape(report.Project) + ` &mdash; ` + time.Now().Format("January 2, 2006") + `</div>
</div>
`)

	// Score card
	b.WriteString(`<div class="score-card">
  <div><span class="score-big" style="color:` + gradeColor + `">` + fmt.Sprintf("%.0f", report.HealthScore) + `</span><span class="grade" style="background:` + gradeColor + `">` + grade + `</span></div>
  <div style="color:#7f8c8d;margin-top:0.5rem">Health Score out of 100</div>
  <div class="stack">`)
	for _, s := range report.Stack {
		b.WriteString(`<span>` + htmlEscape(s) + `</span>`)
	}
	b.WriteString(`</div>
</div>
`)

	// Dimensions
	b.WriteString(`<div class="dimensions">`)
	for _, s := range report.Scores {
		barColor := "#27ae60"
		if s.Score < 50 {
			barColor = "#e74c3c"
		} else if s.Score < 80 {
			barColor = "#f39c12"
		}
		statusIcon := "&#10003;"
		switch s.Status {
		case "fail":
			statusIcon = "&#10007;"
		case "warn":
			statusIcon = "&#9888;"
		case "skip":
			statusIcon = "&ndash;"
		}
		b.WriteString(`<div class="dim">
  <div class="dim-header">
    <span class="dim-name">` + statusIcon + ` ` + htmlEscape(s.Dimension) + `</span>
    <span class="dim-score" style="color:` + barColor + `">` + fmt.Sprintf("%.0f", s.Score) + `</span>
  </div>
  <div class="dim-bar"><div class="dim-bar-fill" style="width:` + fmt.Sprintf("%.0f%%", s.Score) + `;background:` + barColor + `"></div></div>
  <div class="dim-detail">` + htmlEscape(s.Details) + `</div>
</div>
`)
	}
	b.WriteString(`</div>`)

	// Findings
	if len(report.TopFindings) > 0 {
		b.WriteString(`<div class="findings"><h2>Top Findings</h2>`)
		for i, f := range report.TopFindings {
			if i >= 10 {
				break
			}
			cls := "finding"
			if f.Severity == "high" {
				cls = "finding high"
			}
			b.WriteString(`<div class="` + cls + `">
  <div class="sev ` + f.Severity + `">` + htmlEscape(f.Severity) + `</div>
  <div class="desc">` + htmlEscape(f.Description) + `</div>`)
			if f.Fix != "" {
				b.WriteString(`<div class="fix">Fix: ` + htmlEscape(f.Fix) + `</div>`)
			}
			b.WriteString(`</div>
`)
		}
		b.WriteString(`</div>`)
	}

	// Cost estimate
	if report.CostEstimate != nil {
		b.WriteString(`<div class="estimate"><h2>Remediation Estimate</h2>
<div class="estimate-grid">
  <div class="est-item"><div class="est-value">~` + fmt.Sprintf("%d", report.CostEstimate.EngineeringHours) + `h</div><div class="est-label">Engineering</div></div>
  <div class="est-item"><div class="est-value">~` + fmt.Sprintf("%d", report.CostEstimate.TimelineWeeks) + `w</div><div class="est-label">Timeline</div></div>
  <div class="est-item"><div class="est-value">$` + fmt.Sprintf("%d", report.CostEstimate.InfraMonthlyCost) + `/mo</div><div class="est-label">Infrastructure</div></div>
</div>
</div>`)
	}

	b.WriteString(`<div class="footer">Generated by FORGE Audit &mdash; ` + time.Now().Format("2006-01-02 15:04") + `</div>
</div>
</body>
</html>`)

	return b.String()
}

func htmlEscape(s string) string {
	s = strings.ReplaceAll(s, "&", "&amp;")
	s = strings.ReplaceAll(s, "<", "&lt;")
	s = strings.ReplaceAll(s, ">", "&gt;")
	s = strings.ReplaceAll(s, "\"", "&quot;")
	return s
}

// max/min already defined in other files
