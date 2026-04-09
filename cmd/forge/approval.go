package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// approvalCmd represents the approval noun
var approvalCmd = &cobra.Command{
	Use:   "approval",
	Short: "Manage approvals - human checkpoints",
	Long: `Approvals are human-in-the-loop checkpoints for risky operations.

Each approval has:
  - ID: Unique identifier
  - Type: merge, deploy, feature, release
  - Tier: watch, phone, desktop (notification method)
  - Domain/Project: Where the approval is needed
  - Confidence: ML recommendation (0.0 - 1.0)
  - Risk Score: Estimated risk level (0.0 - 1.0)
  - Status: pending, approved, rejected, expired

Universal verbs:
  list, show, decide

Examples:
  # List pending approvals
  forge approval list --pending

  # Show approval details
  forge approval show 01APP456

  # Approve a request
  forge approval decide 01APP456 --approve

  # Reject a request
  forge approval decide 01APP456 --reject --reason "Tests failing"`,
}

// approvalListCmd: forge approval list
var approvalListCmd = &cobra.Command{
	Use:   "list",
	Short: "List approvals",
	Long:  "List approvals with optional filtering.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		pendingOnly, _ := cmd.Flags().GetBool("pending")
		domain, _ := cmd.Flags().GetString("domain")

		// Determine status filter for API call.
		statusFilter := ""
		if pendingOnly {
			statusFilter = "pending"
		}

		// Try live v3 API first.
		apiClient := internal.NewClient()
		approvals, apiErr := apiClient.ListApprovals(cmd.Context(), statusFilter)
		if apiErr != nil {
			// Daemon error — fall back to local file.
			var unreachable *internal.DaemonUnreachableError
			if errors.As(apiErr, &unreachable) {
				fmt.Fprintf(os.Stderr, "Warning: daemon unreachable — showing local approval data\n  Recovery: forge daemon start\n")
			} else {
				fmt.Fprintf(os.Stderr, "warning: approval API error: %v\n", apiErr)
			}
			local, localErr := loadApprovals()
			if localErr != nil {
				return fmt.Errorf("failed to load approvals: %w", apiErr)
			}
			approvals = local
			// Apply pending filter to fallback data when not already filtered above.
			if pendingOnly {
				var filtered []internal.Approval
				for _, a := range approvals {
					if a.Status == "pending" {
						filtered = append(filtered, a)
					}
				}
				approvals = filtered
			}
		}

		// Filter by domain if specified.
		if domain != "" {
			var filtered []internal.Approval
			for _, a := range approvals {
				if a.Domain == domain {
					filtered = append(filtered, a)
				}
			}
			approvals = filtered
		}

		formatter := internal.NewFormatter(format, nil)
		if err := formatter.FormatApprovals(approvals); err != nil {
			return err
		}

		if format == "table" {
			formatter.Printf("\nTotal: %d approvals\n", len(approvals))
		}
		return nil
	},
}

// approvalShowCmd: forge approval show <id>
var approvalShowCmd = &cobra.Command{
	Use:   "show [id]",
	Short: "Show approval details",
	Long:  "Display detailed information about a specific approval.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		approvalID := args[0]
		format, _ := cmd.Flags().GetString("format")

		// Try live v3 API first — list all and find by ID.
		apiClient := internal.NewClient()
		apiApprovals, apiErr := apiClient.ListApprovals(cmd.Context(), "")

		var approval *internal.Approval

		if apiErr == nil {
			// Search API results.
			for i := range apiApprovals {
				if apiApprovals[i].ID == approvalID {
					approval = &apiApprovals[i]
					break
				}
			}
		}

		// If not found in API results (any error, or ID not in list), fall back
		// to local file / demo data.
		if approval == nil {
			localApprovals, localErr := loadApprovals()
			if localErr != nil {
				if apiErr != nil {
					return fmt.Errorf("failed to fetch approvals: %w", apiErr)
				}
				return fmt.Errorf("approval not found: %s", approvalID)
			}
			for i := range localApprovals {
				if localApprovals[i].ID == approvalID {
					approval = &localApprovals[i]
					break
				}
			}
		}

		if approval == nil {
			return fmt.Errorf("approval not found: %s", approvalID)
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatApproval(approval)
	},
}

// approvalDecideCmd: forge approval decide <id> --approve|--reject
var approvalDecideCmd = &cobra.Command{
	Use:   "decide [id]",
	Short: "Decide on an approval",
	Long:  "Approve or reject an approval request.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		approvalID := args[0]
		approve, _ := cmd.Flags().GetBool("approve")
		reject, _ := cmd.Flags().GetBool("reject")
		reason, _ := cmd.Flags().GetString("reason")
		format, _ := cmd.Flags().GetString("format")

		if !approve && !reject {
			return fmt.Errorf("must specify either --approve or --reject")
		}

		decision := "approve"
		if reject {
			decision = "reject"
		}

		// Try live v3 API first.
		apiClient := internal.NewClient()
		user := "cli-user"

		var apiErr error
		if decision == "approve" {
			apiErr = apiClient.ApproveApproval(cmd.Context(), approvalID, user)
		} else {
			apiErr = apiClient.RejectApproval(cmd.Context(), approvalID, user)
		}

		if apiErr != nil {
			// Fall back to local file mutation for any API error (daemon
			// unreachable, approval not in v3 DB, etc.).
			approvals, localErr := loadApprovals()
			if localErr != nil {
				return fmt.Errorf("failed to %s approval: %w", decision, apiErr)
			}

			var found bool
			for i := range approvals {
				if approvals[i].ID == approvalID {
					if approvals[i].Status != "pending" {
						return fmt.Errorf("approval %s is already %s", approvalID, approvals[i].Status)
					}
					approvals[i].Status = decision
					now := time.Now()
					approvals[i].DecidedAt = &now
					approvals[i].DecidedBy = user
					approvals[i].Decision = decision
					approvals[i].Reason = reason
					found = true
					break
				}
			}

			if !found {
				return fmt.Errorf("approval not found: %s\n  Recovery: forge daemon start (approval may exist only in daemon DB)", approvalID)
			}

			if err := saveApprovals(approvals); err != nil {
				return fmt.Errorf("failed to save approvals: %w", err)
			}
		}

		formatter := internal.NewFormatter(format, nil)
		if decision == "approve" {
			formatter.Printf("Approved: %s\n", approvalID)
		} else {
			formatter.Printf("Rejected: %s\n", approvalID)
			if reason != "" {
				formatter.Printf("Reason: %s\n", reason)
			}
		}
		return nil
	},
}

// loadApprovals loads approvals from the approvals directory
func loadApprovals() ([]internal.Approval, error) {
	path := filepath.Join(".forge", "approvals", "approvals.json")
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil // No approvals file = no approvals
		}
		return nil, err
	}
	var approvals []internal.Approval
	if err := json.Unmarshal(data, &approvals); err != nil {
		return nil, err
	}
	return approvals, nil
}

// saveApprovals saves approvals to .forge/approvals/approvals.json
func saveApprovals(approvals []internal.Approval) error {
	dir := filepath.Join(".forge", "approvals")
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(approvals, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, "approvals.json"), data, 0644)
}


// approvalBulkDecideCmd: forge approval bulk-decide [--threshold 0.95] [--dry-run]
var approvalBulkDecideCmd = &cobra.Command{
	Use:   "bulk-decide",
	Short: "Auto-approve pending approvals above a confidence threshold",
	Long: `Scan all pending approvals and automatically approve those whose confidence
score meets or exceeds the given threshold.

Use --dry-run to preview what would be approved without making any API calls.

Examples:
  # Auto-approve all pending approvals with confidence >= 0.95 (default)
  forge approval bulk-decide

  # Use a custom threshold
  forge approval bulk-decide --threshold 0.90

  # Preview without approving
  forge approval bulk-decide --dry-run`,
	RunE: func(cmd *cobra.Command, args []string) error {
		threshold, _ := cmd.Flags().GetFloat64("threshold")
		dryRun, _ := cmd.Flags().GetBool("dry-run")

		apiClient := internal.NewClient()

		// Fetch all pending approvals.
		approvals, apiErr := apiClient.ListApprovals(cmd.Context(), "pending")
		if apiErr != nil {
			// Fall back to local data on API error only.
			var unreachable *internal.DaemonUnreachableError
			if !errors.As(apiErr, &unreachable) {
				fmt.Fprintf(os.Stderr, "warning: approval API error: %v\n", apiErr)
			}
			local, localErr := loadApprovals()
			if localErr != nil {
				return fmt.Errorf("failed to load approvals: %w", apiErr)
			}
			// Filter to pending only from local data.
			approvals = nil
			for _, a := range local {
				if a.Status == "pending" {
					approvals = append(approvals, a)
				}
			}
		}

		fmt.Printf("Scanning %d pending approvals...\n\n", len(approvals))

		// Partition into above-threshold and below-threshold.
		var toApprove []internal.Approval
		var skipped []internal.Approval
		for _, a := range approvals {
			if a.Confidence >= threshold {
				toApprove = append(toApprove, a)
			} else {
				skipped = append(skipped, a)
			}
		}

		// Print approvals section.
		dryLabel := ""
		if dryRun {
			dryLabel = " (dry-run)"
		}

		if len(toApprove) > 0 {
			if dryRun {
				fmt.Printf("WOULD APPROVE%s (%d):\n", dryLabel, len(toApprove))
			} else {
				fmt.Printf("APPROVED (%d):\n", len(toApprove))
			}

			approved := 0
			for _, a := range toApprove {
				project := a.Project
				if project == "" {
					project = a.Domain
				}
				if !dryRun {
					err := apiClient.ApproveApproval(cmd.Context(), a.ID, "cli-bulk")
					if err != nil {
						fmt.Printf("  %-14s  %-8s  %-20s  confidence=%.2f  FAILED: %v\n",
							a.ID, a.Type, project, a.Confidence, err)
						continue
					}
					approved++
					fmt.Printf("  %-14s  %-8s  %-20s  confidence=%.2f  +\n",
						a.ID, a.Type, project, a.Confidence)
				} else {
					fmt.Printf("  %-14s  %-8s  %-20s  confidence=%.2f\n",
						a.ID, a.Type, project, a.Confidence)
					approved++
				}
			}
			_ = approved
		} else {
			fmt.Printf("APPROVED (0): none above threshold %.2f\n", threshold)
		}

		// Print skipped section.
		if len(skipped) > 0 {
			fmt.Printf("\nSKIPPED -- below threshold %.2f (%d):\n", threshold, len(skipped))
			for _, a := range skipped {
				project := a.Project
				if project == "" {
					project = a.Domain
				}
				fmt.Printf("  %-14s  %-8s  %-20s  confidence=%.2f\n",
					a.ID, a.Type, project, a.Confidence)
			}
		}

		// Summary line.
		total := len(approvals)
		approvedCount := len(toApprove)
		action := "approved"
		if dryRun {
			action = "would approve"
		}
		fmt.Printf("\nSummary: %s %d/%d (threshold=%.2f)\n", action, approvedCount, total, threshold)
		return nil
	},
}

func init() {
	// approval list flags
	approvalListCmd.Flags().Bool("pending", false, "Show only pending approvals")
	approvalListCmd.Flags().String("domain", "", "Filter by domain")

	// approval decide flags
	approvalDecideCmd.Flags().Bool("approve", false, "Approve the request")
	approvalDecideCmd.Flags().Bool("reject", false, "Reject the request")
	approvalDecideCmd.Flags().String("reason", "", "Reason for decision")

	// approval bulk-decide flags
	approvalBulkDecideCmd.Flags().Float64("threshold", 0.95, "Minimum confidence score to auto-approve (0.0-1.0)")
	approvalBulkDecideCmd.Flags().Bool("dry-run", false, "Show what would be approved without making API calls")

	// Add commands to approval noun
	approvalCmd.AddCommand(approvalListCmd)
	approvalCmd.AddCommand(approvalShowCmd)
	approvalCmd.AddCommand(approvalDecideCmd)
	approvalCmd.AddCommand(approvalBulkDecideCmd)
}
