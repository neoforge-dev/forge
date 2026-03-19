package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

type blueprintStepDef struct {
	Type     string   `yaml:"type" json:"type"`
	Name     string   `yaml:"name" json:"name"`
	Command  string   `yaml:"command,omitempty" json:"command,omitempty"`
	Agent    string   `yaml:"agent,omitempty" json:"agent,omitempty"`
	Evidence []string `yaml:"evidence,omitempty" json:"evidence,omitempty"`
}

type blueprintDef struct {
	ID          string             `yaml:"id" json:"id"`
	Name        string             `yaml:"name" json:"name"`
	Description string             `yaml:"description" json:"description"`
	Steps       []blueprintStepDef `yaml:"steps" json:"steps"`
}

type blueprintRunResponse struct {
	ID          string `json:"id"`
	TaskID      string `json:"task_id"`
	BlueprintID string `json:"blueprint_id"`
	Status      string `json:"status"`
	CurrentStep int    `json:"current_step"`
	Error       string `json:"error,omitempty"`
}

var blueprintCmd = &cobra.Command{
	Use:   "blueprint",
	Short: "Validate and run durable task blueprints",
	Long:  "Blueprints define durable, resumable task-linked execution flows for FORGE.",
}

var blueprintValidateCmd = &cobra.Command{
	Use:   "validate <file-or-id>",
	Short: "Validate a blueprint YAML file or configured blueprint ID",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		path, bp, err := loadBlueprintForCLI(args[0])
		if err != nil {
			return err
		}
		if err := validateBlueprintForCLI(bp); err != nil {
			return err
		}
		fmt.Printf("OK  %s  (%d steps)\n", path, len(bp.Steps))
		return nil
	},
}

var blueprintRunCmd = &cobra.Command{
	Use:   "run <blueprint-id>",
	Short: "Start a blueprint run for a task",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID, _ := cmd.Flags().GetString("task")
		if taskID == "" {
			return fmt.Errorf("--task is required")
		}
		client := internal.NewClient()
		resp, err := client.Post(context.Background(), "/blueprints/runs", map[string]string{
			"task_id":      taskID,
			"blueprint_id": args[0],
		})
		if err != nil {
			return err
		}
		if err := internal.CheckResponse(resp); err != nil {
			return err
		}
		var run blueprintRunResponse
		if err := internal.DecodeJSON(resp, &run); err != nil {
			return err
		}
		if outputFormat == "json" {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(run)
		}
		fmt.Printf("Run ID: %s\nTask: %s\nBlueprint: %s\nStatus: %s\nCurrent Step: %d\n", run.ID, run.TaskID, run.BlueprintID, run.Status, run.CurrentStep)
		if run.Error != "" {
			fmt.Printf("Error: %s\n", run.Error)
		}
		return nil
	},
}

var blueprintStatusCmd = &cobra.Command{
	Use:   "status <run-id>",
	Short: "Show the status of a blueprint run",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		client := internal.NewClient()
		resp, err := client.Get(context.Background(), "/blueprints/runs/"+args[0])
		if err != nil {
			return err
		}
		if err := internal.CheckResponse(resp); err != nil {
			return err
		}
		var run map[string]interface{}
		if err := internal.DecodeJSON(resp, &run); err != nil {
			return err
		}
		if outputFormat == "json" {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(run)
		}
		fmt.Printf("Run ID: %v\nStatus: %v\nCurrent Step: %v\n", run["id"], run["status"], run["current_step"])
		if run["blueprint_id"] != nil {
			fmt.Printf("Blueprint: %v\n", run["blueprint_id"])
		}
		if run["task_id"] != nil {
			fmt.Printf("Task: %v\n", run["task_id"])
		}
		if errText, ok := run["error"].(string); ok && errText != "" {
			fmt.Printf("Error: %s\n", errText)
		}
		return nil
	},
}

var blueprintListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all configured blueprints",
	RunE: func(cmd *cobra.Command, args []string) error {
		client := internal.NewClient()
		resp, err := client.Get(context.Background(), "/blueprints")
		if err != nil {
			return err
		}
		if err := internal.CheckResponse(resp); err != nil {
			return err
		}
		var result struct {
			Blueprints []blueprintDef `json:"blueprints"`
			Count      int            `json:"count"`
		}
		if err := internal.DecodeJSON(resp, &result); err != nil {
			return err
		}
		if outputFormat == "json" {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(result)
		}
		if result.Count == 0 {
			fmt.Println("No blueprints found")
			return nil
		}
		fmt.Printf("%-30s %-40s %s\n", "ID", "NAME", "STEPS")
		fmt.Println(strings.Repeat("-", 80))
		for _, bp := range result.Blueprints {
			name := bp.Name
			if name == "" {
				name = "(unnamed)"
			}
			fmt.Printf("%-30s %-40s %d\n", bp.ID, name, len(bp.Steps))
		}
		fmt.Printf("\nTotal: %d blueprints\n", result.Count)
		return nil
	},
}

var blueprintResumeCmd = &cobra.Command{
	Use:   "resume <run-id>",
	Short: "Resume a paused or failed blueprint run",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		client := internal.NewClient()
		resp, err := client.Post(context.Background(), "/blueprints/runs/"+args[0]+"/resume", nil)
		if err != nil {
			return err
		}
		if err := internal.CheckResponse(resp); err != nil {
			return err
		}
		var run blueprintRunResponse
		if err := internal.DecodeJSON(resp, &run); err != nil {
			return err
		}
		if outputFormat == "json" {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(run)
		}
		fmt.Printf("Run resumed: %s\n", run.ID)
		fmt.Printf("Blueprint: %s\n", run.BlueprintID)
		fmt.Printf("Task: %s\n", run.TaskID)
		fmt.Printf("Status: %s\n", run.Status)
		fmt.Printf("Current Step: %d\n", run.CurrentStep)
		if run.Error != "" {
			fmt.Printf("Error: %s\n", run.Error)
		}
		return nil
	},
}

var blueprintShowCmd = &cobra.Command{
	Use:   "show <id>",
	Short: "Show blueprint details",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		client := internal.NewClient()
		resp, err := client.Get(context.Background(), "/blueprints")
		if err != nil {
			return err
		}
		if err := internal.CheckResponse(resp); err != nil {
			return err
		}
		var result struct {
			Blueprints []blueprintDef `json:"blueprints"`
		}
		if err := internal.DecodeJSON(resp, &result); err != nil {
			return err
		}
		var bp *blueprintDef
		for _, b := range result.Blueprints {
			if b.ID == args[0] {
				bp = &b
				break
			}
		}
		if bp == nil {
			return fmt.Errorf("blueprint not found: %s", args[0])
		}
		if outputFormat == "json" {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(bp)
		}
		fmt.Printf("ID:          %s\n", bp.ID)
		fmt.Printf("Name:        %s\n", bp.Name)
		if bp.Description != "" {
			fmt.Printf("Description: %s\n", bp.Description)
		}
		fmt.Printf("\nSteps (%d):\n", len(bp.Steps))
		for i, step := range bp.Steps {
			fmt.Printf("  %d. [%s] %s\n", i+1, step.Type, step.Name)
		}
		return nil
	},
}

func loadBlueprintForCLI(ref string) (string, *blueprintDef, error) {
	path := resolveBlueprintPathForCLI(ref)
	data, err := os.ReadFile(path)
	if err != nil {
		return path, nil, fmt.Errorf("read blueprint %s: %w", ref, err)
	}
	var bp blueprintDef
	if err := yaml.Unmarshal(data, &bp); err != nil {
		return path, nil, fmt.Errorf("parse blueprint %s: %w", ref, err)
	}
	if bp.ID == "" {
		bp.ID = ref
	}
	return path, &bp, nil
}

func resolveBlueprintPathForCLI(ref string) string {
	if strings.HasSuffix(ref, ".yaml") || strings.HasSuffix(ref, ".yml") {
		if filepath.IsAbs(ref) {
			return ref
		}
		root := findForgeRoot()
		if root == "" {
			return ref
		}
		return filepath.Join(root, ref)
	}
	root := findForgeRoot()
	if root == "" {
		root = "."
	}
	return filepath.Join(root, "config", "blueprints", ref+".yaml")
}

func validateBlueprintForCLI(bp *blueprintDef) error {
	if bp == nil {
		return fmt.Errorf("blueprint is nil")
	}
	if strings.TrimSpace(bp.ID) == "" {
		return fmt.Errorf("missing blueprint id")
	}
	if len(bp.Steps) == 0 {
		return fmt.Errorf("blueprint must have at least one step")
	}
	seen := map[string]bool{}
	for i, step := range bp.Steps {
		if step.Name == "" {
			return fmt.Errorf("step %d missing name", i)
		}
		if seen[step.Name] {
			return fmt.Errorf("duplicate step name: %s", step.Name)
		}
		seen[step.Name] = true
		switch step.Type {
		case "check", "shell":
			if strings.TrimSpace(step.Command) == "" {
				return fmt.Errorf("step %s requires command", step.Name)
			}
		case "dispatch", "review", "complete":
		default:
			return fmt.Errorf("step %s has unsupported type %s", step.Name, step.Type)
		}
	}
	return nil
}

func init() {
	blueprintRunCmd.Flags().String("task", "", "Task ID to attach the blueprint run to")
	blueprintCmd.AddCommand(blueprintValidateCmd)
	blueprintCmd.AddCommand(blueprintRunCmd)
	blueprintCmd.AddCommand(blueprintStatusCmd)
	blueprintCmd.AddCommand(blueprintListCmd)
	blueprintCmd.AddCommand(blueprintResumeCmd)
	blueprintCmd.AddCommand(blueprintShowCmd)
	rootCmd.AddCommand(blueprintCmd)
}
