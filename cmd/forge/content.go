package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
)

var contentCmd = &cobra.Command{
	Use:   "content",
	Short: "Content pipeline operations",
	Long:  "Manage content drafts from fleet agents — publish, list, and review.",
}

// contentPublishCmd copies a draft from .forge/heartbeat/results/ to the target content directory.
var contentPublishCmd = &cobra.Command{
	Use:   "publish <results-file> <target-dir>",
	Short: "Publish a fleet agent's content draft to a content directory",
	Long: `Copies a content file from .forge/heartbeat/results/ to the specified target directory.

Examples:
  # Publish a blog post draft to IS content
  forge content publish minimax-is-blog-post.md services/interview-simulator/content/blog/

  # Publish with a different filename
  forge content publish kilo-bb-spanish-posts.md apps/babybites-landing/blog/ --as blw-guide.md`,
	Args: cobra.ExactArgs(2),
	RunE: func(cmd *cobra.Command, args []string) error {
		root := findForgeRoot()
		srcName := args[0]
		targetDir := args[1]

		// Resolve source: check if it's a full path or just a filename
		srcPath := srcName
		if !filepath.IsAbs(srcPath) && !strings.Contains(srcPath, "/") {
			srcPath = filepath.Join(root, ".forge", "heartbeat", "results", srcName)
		}

		if _, err := os.Stat(srcPath); err != nil {
			return fmt.Errorf("source not found: %s\n\n  Fix: check 'forge content list' for available drafts", srcPath)
		}

		// Resolve target directory
		if !filepath.IsAbs(targetDir) {
			targetDir = filepath.Join(root, targetDir)
		}
		if err := os.MkdirAll(targetDir, 0755); err != nil {
			return fmt.Errorf("cannot create target directory: %w", err)
		}

		// Determine output filename
		outName, _ := cmd.Flags().GetString("as")
		if outName == "" {
			outName = filepath.Base(srcPath)
		}
		destPath := filepath.Join(targetDir, outName)

		// Check for overwrites
		if _, err := os.Stat(destPath); err == nil {
			force, _ := cmd.Flags().GetBool("force")
			if !force {
				return fmt.Errorf("target exists: %s\n\n  Fix: use --force to overwrite", destPath)
			}
		}

		// Copy file
		src, err := os.Open(srcPath)
		if err != nil {
			return fmt.Errorf("open source: %w", err)
		}
		defer src.Close()

		dst, err := os.Create(destPath)
		if err != nil {
			return fmt.Errorf("create target: %w", err)
		}
		defer dst.Close()

		n, err := io.Copy(dst, src)
		if err != nil {
			return fmt.Errorf("copy: %w", err)
		}

		fmt.Printf("Published: %s → %s (%d bytes)\n", srcName, destPath, n)
		return nil
	},
}

// contentListCmd lists available content drafts in .forge/heartbeat/results/.
var contentListCmd = &cobra.Command{
	Use:   "list",
	Short: "List content drafts from fleet agents",
	RunE: func(cmd *cobra.Command, args []string) error {
		root := findForgeRoot()
		resultsDir := filepath.Join(root, ".forge", "heartbeat", "results")

		entries, err := os.ReadDir(resultsDir)
		if err != nil {
			return fmt.Errorf("cannot read results directory: %w\n\n  Fix: ensure .forge/heartbeat/results/ exists", err)
		}

		if len(entries) == 0 {
			fmt.Println("No content drafts found.")
			return nil
		}

		fmt.Printf("%-50s %8s\n", "FILE", "SIZE")
		for _, e := range entries {
			if e.IsDir() {
				continue
			}
			info, _ := e.Info()
			size := int64(0)
			if info != nil {
				size = info.Size()
			}
			fmt.Printf("%-50s %6dB\n", e.Name(), size)
		}
		fmt.Printf("\nTotal: %d files in .forge/heartbeat/results/\n", len(entries))
		return nil
	},
}

func init() {
	contentPublishCmd.Flags().String("as", "", "Rename the file on publish")
	contentPublishCmd.Flags().Bool("force", false, "Overwrite existing files")
	contentCmd.AddCommand(contentPublishCmd)
	contentCmd.AddCommand(contentListCmd)
}
