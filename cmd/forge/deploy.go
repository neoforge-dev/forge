// deploy.go — forge deploy: one-command product deployment (Rails 8 Phase 6)
package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"

	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

// ---------------------------------------------------------------------------
// Config types
// ---------------------------------------------------------------------------

// DeployConfig is the top-level structure parsed from config/deploys.yaml.
type DeployConfig struct {
	Products map[string]DeployProduct `yaml:"products"`
}

// DeployProduct describes one deployable product.
type DeployProduct struct {
	Domain   string          `yaml:"domain"`
	Backend  *DeployBackend  `yaml:"backend,omitempty"`
	Frontend *DeployFrontend `yaml:"frontend,omitempty"`
}

// DeployBackend describes a backend service deployment.
type DeployBackend struct {
	Type         string `yaml:"type"`
	Directory    string `yaml:"directory"`
	PreDeploy    string `yaml:"pre_deploy,omitempty"`
	StartCommand string `yaml:"start_command,omitempty"`
}

// DeployFrontend describes a frontend deployment.
type DeployFrontend struct {
	Type         string `yaml:"type"`
	Directory    string `yaml:"directory"`
	BuildCommand string `yaml:"build_command,omitempty"`
	OutputDir    string `yaml:"output_dir,omitempty"`
	ProjectName  string `yaml:"project_name,omitempty"`
}

// ---------------------------------------------------------------------------
// ANSI colors — deploy uses its own set to avoid redeclaring doctor.go vars.
// ---------------------------------------------------------------------------

var (
	deployColorReset  = "\033[0m"
	deployColorGreen  = "\033[32m"
	deployColorRed    = "\033[31m"
	deployColorYellow = "\033[33m"
	deployColorCyan   = "\033[36m"
	deployColorBold   = "\033[1m"
)

func init() {
	if os.Getenv("NO_COLOR") != "" {
		deployColorReset = ""
		deployColorGreen = ""
		deployColorRed = ""
		deployColorYellow = ""
		deployColorCyan = ""
		deployColorBold = ""
	}
}

// ---------------------------------------------------------------------------
// Helper: load config
// ---------------------------------------------------------------------------

// resolveDeployConfigPath returns the path to config/deploys.yaml, searching
// upward from cwd the same way findForgeRoot does.
func resolveDeployConfigPath() string {
	// 1. Explicit env override
	if p := os.Getenv("FORGE_DEPLOY_CONFIG"); p != "" {
		return p
	}

	// 2. Walk up from cwd looking for a config/deploys.yaml beside a .forge dir
	dir, err := os.Getwd()
	if err != nil {
		return "config/deploys.yaml"
	}
	for {
		candidate := filepath.Join(dir, "config", "deploys.yaml")
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}

	return "config/deploys.yaml"
}

// loadDeployConfig reads and parses config/deploys.yaml (or the path returned
// by resolveDeployConfigPath).
func loadDeployConfig() (*DeployConfig, error) {
	path := resolveDeployConfigPath()
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("cannot read deploy config %q: %w\n  Fix: ensure config/deploys.yaml exists in the FORGE root", path, err)
	}
	var cfg DeployConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("invalid YAML in %q: %w", path, err)
	}
	return &cfg, nil
}

// ---------------------------------------------------------------------------
// Step runner
// ---------------------------------------------------------------------------

// deployStep describes one deployment action.
type deployStep struct {
	label   string // e.g. "[backend]"
	desc    string // e.g. "Running pre-deploy: alembic upgrade head"
	dir     string // working directory for the command
	cmdArgs []string
}

// printStep prints the step header in a consistent style.
func printStep(label, desc string) {
	fmt.Printf("%s%s%s %s\n", deployColorCyan+deployColorBold, label, deployColorReset, desc)
}

// runStep executes a deploy step, streaming stdout/stderr to the terminal.
// Returns an error with a recovery hint on failure.
func runStep(step deployStep) error {
	printStep(step.label, step.desc)

	if len(step.cmdArgs) == 0 {
		return nil
	}

	cmd := exec.Command(step.cmdArgs[0], step.cmdArgs[1:]...)
	cmd.Dir = step.dir
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Env = os.Environ()

	if err := cmd.Run(); err != nil {
		// Distinguish "binary not found" from "command failed"
		if strings.Contains(err.Error(), "executable file not found") ||
			strings.Contains(err.Error(), "no such file or directory") {
			return fmt.Errorf(
				"%s%s%s command not found: %q\n  Fix: install %s (e.g. npm install -g %s or pip install %s)",
				deployColorRed, "error", deployColorReset,
				step.cmdArgs[0], step.cmdArgs[0], step.cmdArgs[0], step.cmdArgs[0],
			)
		}
		return fmt.Errorf(
			"%s%s%s step %q failed: %w\n  Fix: check the output above and resolve the error before retrying",
			deployColorRed, "error", deployColorReset,
			step.desc, err,
		)
	}

	fmt.Printf("%s✓%s %s done\n", deployColorGreen, deployColorReset, step.label)
	return nil
}

// ---------------------------------------------------------------------------
// Step builders
// ---------------------------------------------------------------------------

// backendSteps returns the ordered deployment steps for a backend component.
func backendSteps(name string, b *DeployBackend) []deployStep {
	var steps []deployStep

	if b.PreDeploy != "" {
		parts := shellSplit(b.PreDeploy)
		steps = append(steps, deployStep{
			label:   "[backend]",
			desc:    "Running pre-deploy: " + b.PreDeploy,
			dir:     b.Directory,
			cmdArgs: parts,
		})
	}

	switch b.Type {
	case "railway":
		steps = append(steps, deployStep{
			label:   "[backend]",
			desc:    fmt.Sprintf("Deploying %s backend via Railway", name),
			dir:     b.Directory,
			cmdArgs: []string{"railway", "up"},
		})
	default:
		steps = append(steps, deployStep{
			label: "[backend]",
			desc:  fmt.Sprintf("Unknown backend type %q — skipping deploy step", b.Type),
		})
	}

	return steps
}

// frontendSteps returns the ordered deployment steps for a frontend component.
func frontendSteps(name string, f *DeployFrontend) []deployStep {
	var steps []deployStep

	if f.BuildCommand != "" {
		parts := shellSplit(f.BuildCommand)
		steps = append(steps, deployStep{
			label:   "[frontend]",
			desc:    "Building: " + f.BuildCommand,
			dir:     f.Directory,
			cmdArgs: parts,
		})
	}

	switch f.Type {
	case "cloudflare-pages":
		outputDir := f.OutputDir
		if outputDir == "" {
			outputDir = "dist"
		}
		args := []string{"npx", "wrangler", "pages", "deploy", outputDir}
		if f.ProjectName != "" {
			args = append(args, "--project-name", f.ProjectName)
		}
		steps = append(steps, deployStep{
			label:   "[frontend]",
			desc:    fmt.Sprintf("Deploying %s frontend via Cloudflare Pages", name),
			dir:     f.Directory,
			cmdArgs: args,
		})
	default:
		steps = append(steps, deployStep{
			label: "[frontend]",
			desc:  fmt.Sprintf("Unknown frontend type %q — skipping deploy step", f.Type),
		})
	}

	return steps
}

// ---------------------------------------------------------------------------
// Deploy executor
// ---------------------------------------------------------------------------

type deployOptions struct {
	dryRun       bool
	backendOnly  bool
	frontendOnly bool
}

// deployProduct executes (or dry-runs) all steps for a single product.
func deployProduct(name string, product DeployProduct, opts deployOptions) error {
	fmt.Printf("\n%s%s Deploying: %s%s\n", deployColorBold, deployColorCyan, name, deployColorReset)
	if product.Domain != "" {
		fmt.Printf("  domain: %s\n\n", product.Domain)
	}

	var steps []deployStep

	if !opts.frontendOnly && product.Backend != nil {
		steps = append(steps, backendSteps(name, product.Backend)...)
	}
	if !opts.backendOnly && product.Frontend != nil {
		steps = append(steps, frontendSteps(name, product.Frontend)...)
	}

	if len(steps) == 0 {
		fmt.Printf("%s  (no components to deploy for %q with current flags)%s\n",
			deployColorYellow, name, deployColorReset)
		return nil
	}

	for _, step := range steps {
		if opts.dryRun {
			label := step.label
			if label == "" {
				label = "[step]"
			}
			fmt.Printf("  %s[dry-run]%s %s %s\n",
				deployColorYellow, deployColorReset, label, step.desc)
			if len(step.cmdArgs) > 0 {
				fmt.Printf("           cmd: %s\n", strings.Join(step.cmdArgs, " "))
				if step.dir != "" {
					fmt.Printf("           dir: %s\n", step.dir)
				}
			}
			continue
		}

		if err := runStep(step); err != nil {
			return fmt.Errorf("product %q: %w", name, err)
		}
	}

	if !opts.dryRun {
		fmt.Printf("\n%s✓ %s deployed successfully%s\n", deployColorGreen, name, deployColorReset)
	}
	return nil
}

// ---------------------------------------------------------------------------
// shellSplit splits a simple shell command string into tokens.
// It handles single-quoted and double-quoted strings but is intentionally
// simple — not a full POSIX parser.
// ---------------------------------------------------------------------------
func shellSplit(s string) []string {
	var tokens []string
	var current strings.Builder
	inSingle := false
	inDouble := false

	for _, ch := range s {
		switch {
		case ch == '\'' && !inDouble:
			inSingle = !inSingle
		case ch == '"' && !inSingle:
			inDouble = !inDouble
		case ch == ' ' && !inSingle && !inDouble:
			if current.Len() > 0 {
				tokens = append(tokens, current.String())
				current.Reset()
			}
		default:
			current.WriteRune(ch)
		}
	}
	if current.Len() > 0 {
		tokens = append(tokens, current.String())
	}
	return tokens
}

// ---------------------------------------------------------------------------
// Cobra command
// ---------------------------------------------------------------------------

var deployCmd = &cobra.Command{
	Use:   "deploy [product]",
	Short: "Deploy one or all products",
	Long: `Deploy portfolio products to their configured hosting targets.

Reads config/deploys.yaml for product → platform mappings.

Supported backend types:
  railway          — deploys via 'railway up' (Railway.app)

Supported frontend types:
  cloudflare-pages — builds then deploys via 'npx wrangler pages deploy'

Examples:
  # Deploy a single product
  forge deploy interview-simulator

  # Preview all steps without executing
  forge deploy --dry-run interview-simulator

  # Deploy all products
  forge deploy --all

  # Deploy only the backend of a product
  forge deploy --backend-only voice-coach

  # Deploy only the frontend of a product
  forge deploy --frontend-only study-flow`,
	Args: cobra.MaximumNArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		deployAll, _ := cmd.Flags().GetBool("all")
		dryRun, _ := cmd.Flags().GetBool("dry-run")
		backendOnly, _ := cmd.Flags().GetBool("backend-only")
		frontendOnly, _ := cmd.Flags().GetBool("frontend-only")

		if backendOnly && frontendOnly {
			return fmt.Errorf("--backend-only and --frontend-only are mutually exclusive")
		}

		if !deployAll && len(args) == 0 {
			return fmt.Errorf("product name is required (or use --all to deploy all products)\n  Usage: forge deploy <product>\n  List:  forge deploy --dry-run --all")
		}

		cfg, err := loadDeployConfig()
		if err != nil {
			return err
		}

		opts := deployOptions{
			dryRun:       dryRun,
			backendOnly:  backendOnly,
			frontendOnly: frontendOnly,
		}

		if deployAll {
			// Sort product names for deterministic output
			names := make([]string, 0, len(cfg.Products))
			for name := range cfg.Products {
				names = append(names, name)
			}
			sort.Strings(names)

			var failed []string
			for _, name := range names {
				product := cfg.Products[name]
				if err := deployProduct(name, product, opts); err != nil {
					fmt.Fprintf(os.Stderr, "%serror:%s %v\n", deployColorRed, deployColorReset, err)
					failed = append(failed, name)
				}
			}
			if len(failed) > 0 {
				return fmt.Errorf("deploy failed for: %s\n  Fix: resolve errors above and re-run the failed products individually",
					strings.Join(failed, ", "))
			}
			return nil
		}

		// Single product
		productName := args[0]
		product, ok := cfg.Products[productName]
		if !ok {
			// List available products for easy recovery
			available := make([]string, 0, len(cfg.Products))
			for name := range cfg.Products {
				available = append(available, name)
			}
			sort.Strings(available)
			return fmt.Errorf("unknown product %q\n  Available: %s\n  Fix: check config/deploys.yaml or use one of the names above",
				productName, strings.Join(available, ", "))
		}

		return deployProduct(productName, product, opts)
	},
}

func init() {
	deployCmd.Flags().Bool("all", false, "Deploy all products defined in config/deploys.yaml")
	deployCmd.Flags().Bool("dry-run", false, "Print steps without executing")
	deployCmd.Flags().Bool("backend-only", false, "Skip frontend deployment")
	deployCmd.Flags().Bool("frontend-only", false, "Skip backend deployment")
}
