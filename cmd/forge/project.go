package main

import (
	"context"
	"fmt"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// projectCmd represents the project noun
var projectCmd = &cobra.Command{
	Use:   "project",
	Short: "Manage projects/repositories",
	Long:  "List, show, and create projects within domains.",
}

// projectListCmd: forge project list
var projectListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all projects",
	Long:  "List all projects, optionally filtered by domain.",
	RunE: func(cmd *cobra.Command, args []string) error {
		domain, _ := cmd.Flags().GetString("domain")
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListProjects(ctx, domain)
		if err != nil {
			return fmt.Errorf("failed to list projects: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if err := formatter.FormatProjects(result.Projects); err != nil {
			return err
		}

		if format == "table" {
			formatter.Printf("Total: %d projects\n", result.Count)
		}
		return nil
	},
}

// projectShowCmd: forge project show <key>
var projectShowCmd = &cobra.Command{
	Use:   "show <key>",
	Short: "Show project details",
	Long:  "Display detailed information about a specific project.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		projectKey := args[0]
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		project, err := client.GetProject(ctx, projectKey)
		if err != nil {
			return fmt.Errorf("failed to get project: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatProject(project)
	},
}

// projectCreateCmd: forge project create --name "..." --domain "..."
var projectCreateCmd = &cobra.Command{
	Use:   "create",
	Short: "Create a new project",
	Long:  "Create a new project in the FORGE system.",
	RunE: func(cmd *cobra.Command, args []string) error {
		name, _ := cmd.Flags().GetString("name")
		key, _ := cmd.Flags().GetString("key")
		domain, _ := cmd.Flags().GetString("domain")
		description, _ := cmd.Flags().GetString("description")
		pType, _ := cmd.Flags().GetString("type")
		format, _ := cmd.Flags().GetString("format")

		if name == "" || domain == "" {
			return fmt.Errorf("--name and --domain are required")
		}
		if key == "" {
			key = name // Simple default
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		req := &internal.CreateProjectRequest{
			Key:         key,
			Name:        name,
			Domain:      domain,
			Description: description,
			Type:        pType,
		}

		project, err := client.CreateProject(ctx, req)
		if err != nil {
			return fmt.Errorf("failed to create project: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(project)
		}
		formatter.Printf("Created: %s\n", project.Key)
		return nil
	},
}

func init() {
	projectCmd.Hidden = true
	// project list flags
	projectListCmd.Flags().String("domain", "", "Filter projects by domain")

	// project create flags
	projectCreateCmd.Flags().String("name", "", "Project name (required)")
	projectCreateCmd.Flags().String("key", "", "Project key (defaults to name)")
	projectCreateCmd.Flags().String("domain", "", "Domain key (required)")
	projectCreateCmd.Flags().String("description", "", "Project description")
	projectCreateCmd.Flags().String("type", "repository", "Project type (repository, service, tool)")

	// Add commands to project noun
	projectCmd.AddCommand(projectListCmd)
	projectCmd.AddCommand(projectShowCmd)
	projectCmd.AddCommand(projectCreateCmd)
}
