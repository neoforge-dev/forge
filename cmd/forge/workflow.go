package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"sync"
	"time"

	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

// WorkflowStep represents a single step in a workflow definition.
type WorkflowStep struct {
	Name     string `yaml:"name"     json:"name"`
	Command  string `yaml:"command"  json:"command"`
	Parallel bool   `yaml:"parallel" json:"parallel"`
}

// WorkflowDef represents the top-level workflow file structure.
type WorkflowDef struct {
	Name  string         `yaml:"name"  json:"name"`
	Steps []WorkflowStep `yaml:"steps" json:"steps"`
}

// workflowCmd: forge workflow — workflow management noun
var workflowCmd = &cobra.Command{
	Use:   "workflow",
	Short: "Manage and run workflow definitions",
	Long:  "Validate and execute YAML/JSON workflow files with step-based execution.",
}

// workflowValidateCmd: forge workflow validate <file>
var workflowValidateCmd = &cobra.Command{
	Use:   "validate <file>",
	Short: "Validate a workflow definition file",
	Long: `Validate a YAML or JSON workflow file for structural correctness.

Rules checked:
  - File must have a "steps" array
  - Each step must have "name" and "command" fields
  - Step names must be unique

Examples:
  forge workflow validate my-workflow.yaml
  forge workflow validate pipeline.json`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		file := args[0]

		wf, parseErr := loadWorkflowFile(file)
		var errs []string

		if parseErr != nil {
			errs = append(errs, "parse error: "+parseErr.Error())
		} else {
			errs = validateWorkflow(wf)
		}

		if len(errs) == 0 {
			fmt.Printf("OK  %s  (%d steps)\n", file, len(wf.Steps))
			return nil
		}

		fmt.Printf("INVALID  %s\n", file)
		for _, e := range errs {
			fmt.Printf("  - %s\n", e)
		}
		return fmt.Errorf("workflow has %d validation error(s)", len(errs))
	},
}

// workflowRunCmd: forge workflow run <file>
var workflowRunCmd = &cobra.Command{
	Use:   "run <file>",
	Short: "Execute a workflow definition file",
	Long: `Execute a YAML or JSON workflow file sequentially (or in parallel for steps
with parallel: true).

Steps are executed with /bin/sh -c. On failure the workflow stops and exits
with a non-zero status code.

Examples:
  forge workflow run my-workflow.yaml
  forge workflow run pipeline.json`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		file := args[0]

		wf, err := loadWorkflowFile(file)
		if err != nil {
			return fmt.Errorf("failed to load workflow: %w\n\nRecovery: ensure the file is valid YAML or JSON", err)
		}

		if errs := validateWorkflow(wf); len(errs) > 0 {
			fmt.Printf("Workflow validation failed:\n")
			for _, e := range errs {
				fmt.Printf("  - %s\n", e)
			}
			return fmt.Errorf("workflow invalid — run 'forge workflow validate %s' for details", file)
		}

		name := wf.Name
		if name == "" {
			name = file
		}
		fmt.Printf("Running workflow: %s (%d steps)\n\n", name, len(wf.Steps))

		return runWorkflow(wf)
	},
}

// loadWorkflowFile reads and parses a YAML or JSON workflow file.
func loadWorkflowFile(path string) (*WorkflowDef, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("cannot read file '%s': %w", path, err)
	}

	var wf WorkflowDef

	// Try JSON first (by checking leading character), fall back to YAML
	trimmed := data
	for len(trimmed) > 0 && (trimmed[0] == ' ' || trimmed[0] == '\t' || trimmed[0] == '\n' || trimmed[0] == '\r') {
		trimmed = trimmed[1:]
	}

	if len(trimmed) > 0 && trimmed[0] == '{' {
		if err := json.Unmarshal(data, &wf); err != nil {
			return nil, fmt.Errorf("JSON parse error: %w", err)
		}
	} else {
		if err := yaml.Unmarshal(data, &wf); err != nil {
			return nil, fmt.Errorf("YAML parse error: %w", err)
		}
	}

	return &wf, nil
}

// validateWorkflow checks structural correctness and returns a list of errors.
func validateWorkflow(wf *WorkflowDef) []string {
	var errs []string

	if len(wf.Steps) == 0 {
		errs = append(errs, "workflow must have at least one step in the 'steps' array")
	}

	seen := make(map[string]bool)
	for i, step := range wf.Steps {
		stepRef := fmt.Sprintf("step[%d]", i)
		if step.Name == "" {
			errs = append(errs, stepRef+": missing required field 'name'")
		} else {
			stepRef = fmt.Sprintf("step '%s'", step.Name)
		}

		if step.Command == "" {
			errs = append(errs, stepRef+": missing required field 'command'")
		}

		if step.Name != "" {
			if seen[step.Name] {
				errs = append(errs, fmt.Sprintf("duplicate step name: '%s'", step.Name))
			}
			seen[step.Name] = true
		}
	}

	return errs
}

// runWorkflow executes all steps in the workflow, respecting parallel flags.
func runWorkflow(wf *WorkflowDef) error {
	i := 0
	for i < len(wf.Steps) {
		// Collect a batch of steps: one sequential or N parallel
		var batch []WorkflowStep
		if wf.Steps[i].Parallel {
			// Gather all consecutive parallel steps
			for i < len(wf.Steps) && wf.Steps[i].Parallel {
				batch = append(batch, wf.Steps[i])
				i++
			}
		} else {
			batch = []WorkflowStep{wf.Steps[i]}
			i++
		}

		if err := executeBatch(batch); err != nil {
			return err
		}
	}
	return nil
}

// executeBatch runs a slice of steps, either sequentially (len==1) or in parallel.
func executeBatch(steps []WorkflowStep) error {
	if len(steps) == 1 {
		return executeStep(steps[0])
	}

	// Parallel execution
	var (
		wg      sync.WaitGroup
		mu      sync.Mutex
		firstErr error
	)

	for _, step := range steps {
		step := step // capture
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := executeStep(step); err != nil {
				mu.Lock()
				if firstErr == nil {
					firstErr = err
				}
				mu.Unlock()
			}
		}()
	}

	wg.Wait()
	return firstErr
}

// executeStep runs a single workflow step and prints status.
func executeStep(step WorkflowStep) error {
	label := step.Name
	if label == "" {
		label = step.Command
	}

	if step.Parallel {
		fmt.Printf("  [parallel] %s\n", label)
	} else {
		fmt.Printf("  %s\n", label)
	}
	fmt.Printf("    $ %s\n", step.Command)

	start := time.Now()
	shellCmd := exec.Command("/bin/sh", "-c", step.Command)
	shellCmd.Stdout = os.Stdout
	shellCmd.Stderr = os.Stderr
	err := shellCmd.Run()
	elapsed := time.Since(start).Round(time.Millisecond)

	if err != nil {
		fmt.Printf("    FAILED (%s): %v\n\n", elapsed, err)
		return fmt.Errorf("step '%s' failed: %w\n\nRecovery: fix the command and re-run: forge workflow run <file>", step.Name, err)
	}

	fmt.Printf("    OK (%s)\n\n", elapsed)
	return nil
}

func init() {
	workflowCmd.AddCommand(workflowValidateCmd)
	workflowCmd.AddCommand(workflowRunCmd)

	rootCmd.AddCommand(workflowCmd)
}
