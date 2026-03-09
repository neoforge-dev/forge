package main

import (
	"context"
	"fmt"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// laneCmd represents the lane noun
var laneCmd = &cobra.Command{
	Use:   "lane",
	Short: "Manage Dark Factory lanes",
	Long:  "List, show, and manage tasks within Dark Factory workflow lanes.",
}

// laneListCmd: forge lane list
var laneListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all lanes",
	Long:  "List all lanes in the Dark Factory workflow.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListLanes(ctx)
		if err != nil {
			return fmt.Errorf("failed to list lanes: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if err := formatter.FormatLanes(result.Lanes); err != nil {
			return err
		}

		if format == "table" {
			formatter.Printf("Total: %d lanes\n", result.Count)
		}
		return nil
	},
}

// laneShowCmd: forge lane show <key>
var laneShowCmd = &cobra.Command{
	Use:   "show <key>",
	Short: "Show lane details",
	Long:  "Display detailed information about a specific lane.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		laneKey := args[0]
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		lane, err := client.GetLane(ctx, laneKey)
		if err != nil {
			return fmt.Errorf("failed to get lane: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatLane(lane)
	},
}

// laneStatusCmd: forge lane status
var laneStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show status of all lanes",
	RunE: func(cmd *cobra.Command, args []string) error {
		return laneListCmd.RunE(cmd, args)
	},
}

// lanePromoteCmd: forge lane promote <task-id>
var lanePromoteCmd = &cobra.Command{
	Use:   "promote <task-id>",
	Short: "Promote a task to the next lane",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID := args[0]
		target, _ := cmd.Flags().GetString("to")
		comment, _ := cmd.Flags().GetString("comment")
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		req := &internal.PromoteTaskRequest{
			TargetLane: target,
			Comment:    comment,
		}

		if err := client.PromoteTask(ctx, taskID, req); err != nil {
			return fmt.Errorf("failed to promote task: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		formatter.Printf("Task %s promoted successfully\n", taskID)
		return nil
	},
}

func init() {
	// lane promote flags
	lanePromoteCmd.Flags().String("to", "", "Target lane key (optional, defaults to next)")
	lanePromoteCmd.Flags().String("comment", "", "Optional comment for promotion")

	// Add commands to lane noun
	laneCmd.AddCommand(laneListCmd)
	laneCmd.AddCommand(laneShowCmd)
	laneCmd.AddCommand(laneStatusCmd)
	laneCmd.AddCommand(lanePromoteCmd)
}
