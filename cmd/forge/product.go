package main

import (
	"context"
	"fmt"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// productCmd is the canonical command group for product management.
// "project" is kept as a deprecated alias pointing to the same underlying API.
var productCmd = &cobra.Command{
	Use:   "product",
	Short: "Manage products (repositories/projects within domains)",
	Long:  "List, show, and create products within domains.\n\nProducts were previously called 'projects'. Use 'forge product' for all new work.",
}

// productListCmd: forge product list [--domain X]
var productListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all products",
	Long:  "List all products, optionally filtered by domain.",
	RunE: func(cmd *cobra.Command, args []string) error {
		domain, _ := cmd.Flags().GetString("domain")
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListProjects(ctx, domain)
		if err != nil {
			return fmt.Errorf("failed to list products: %w\n\nRecovery: ensure the daemon is running with 'forge daemon status'", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if err := formatter.FormatProjects(result.Projects); err != nil {
			return err
		}

		if format == "table" {
			formatter.Printf("Total: %d products\n", result.Count)
		}
		return nil
	},
}

// productShowCmd: forge product show <key>
var productShowCmd = &cobra.Command{
	Use:   "show <key>",
	Short: "Show product details",
	Long:  "Display detailed information about a specific product.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		productKey := args[0]
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		product, err := client.GetProject(ctx, productKey)
		if err != nil {
			return fmt.Errorf("failed to get product %q: %w\n\nRecovery: verify the product key with 'forge product list'", productKey, err)
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatProject(product)
	},
}

func init() {
	// product list flags
	productListCmd.Flags().String("domain", "", "Filter products by domain")

	// Add subcommands to product noun
	productCmd.AddCommand(productListCmd)
	productCmd.AddCommand(productShowCmd)
}
