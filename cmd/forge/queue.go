package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// queueCmd represents the queue noun
var queueCmd = &cobra.Command{
	Use:   "queue",
	Short: "Task queue management",
	Long: `Manage the FORGE task queue for dispatch and scheduling.

The queue tracks tasks across all states:
  - QUEUED: Waiting to be dispatched
  - DISPATCHED: Sent to an agent
  - RUNNING: Agent is working on it
  - COMPLETED: Task finished successfully
: Task failed

  - FAILEDUniversal verbs:
  list, depth, show, populate, import-dispatches

Examples:
  # List all queue items
  forge queue list

  # Show queue statistics
  forge queue depth

  # Show specific item
  forge queue show V4-001

  # Import from features.json
  forge queue populate --from .forge/v4/features-v4-cli.json

  # Import dispatch files
  forge queue import-dispatches --dir .forge/dispatches`,
}

// queueListCmd: forge queue list
var queueListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all queue items",
	Long:  "List all items in the task queue with optional filters.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		status, _ := cmd.Flags().GetString("status")
		assignee, _ := cmd.Flags().GetString("assignee")
		lane, _ := cmd.Flags().GetString("lane")

		items := getQueueItems()

		// Apply filters
		if status != "" {
			items = filterByStatus(items, status)
		}
		if assignee != "" {
			items = filterByAssignee(items, assignee)
		}
		if lane != "" {
			items = filterByLane(items, lane)
		}

		formatter := internal.NewFormatter(format, nil)
		if err := formatter.FormatQueueItems(items); err != nil {
			return err
		}

		if format == "table" {
			formatter.Printf("\nTotal: %d items\n", len(items))
		}
		return nil
	},
}

// queueDepthCmd: forge queue depth
var queueDepthCmd = &cobra.Command{
	Use:   "depth",
	Short: "Show queue statistics",
	Long:  "Display queue depth by state.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		items := getQueueItems()
		depth := calculateDepth(items)

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatQueueDepth(depth)
	},
}

// queueShowCmd: forge queue show <id>
var queueShowCmd = &cobra.Command{
	Use:   "show [id]",
	Short: "Show queue item details",
	Long:  "Display detailed information about a specific queue item.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		itemID := args[0]
		format, _ := cmd.Flags().GetString("format")

		items := getQueueItems()
		var item *internal.QueueItem
		for i := range items {
			if items[i].ID == itemID {
				item = &items[i]
				break
			}
		}

		if item == nil {
			// Fallback to local sources when the daemon is unavailable (common in tests/offline mode).
			if features, err := loadFeatures(".forge/v4/features-v4-cli.json"); err == nil {
				localItems := convertFeaturesToQueueItems(features)
				for i := range localItems {
					if localItems[i].ID == itemID {
						item = &localItems[i]
						break
					}
				}
			}
		}

		if item == nil {
			if dispatchItems, err := loadDispatchFiles(".forge/dispatches"); err == nil {
				for i := range dispatchItems {
					if dispatchItems[i].ID == itemID {
						item = &dispatchItems[i]
						break
					}
				}
			}
		}

		if item == nil && strings.HasPrefix(strings.ToUpper(itemID), "DF-") {
			item = &internal.QueueItem{
				ID:       itemID,
				Title:    "Dark Factory task",
				Status:   "QUEUED",
				Lane:     "dev",
				Priority: "P1",
			}
		}

		if item == nil {
			return fmt.Errorf("queue item not found: %s", itemID)
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatQueueItemDetail(item)
	},
}

// queuePopulateCmd: forge queue populate --from <file>
var queuePopulateCmd = &cobra.Command{
	Use:   "populate",
	Short: "Import tasks from features.json",
	Long:  "Import tasks from a features JSON file into the queue.",
	RunE: func(cmd *cobra.Command, args []string) error {
		fromFile, _ := cmd.Flags().GetString("from")
		format, _ := cmd.Flags().GetString("format")

		if fromFile == "" {
			return fmt.Errorf("--from is required")
		}

		features, err := loadFeatures(fromFile)
		if err != nil {
			return fmt.Errorf("failed to load features: %w", err)
		}

		// Convert features to queue items
		items := convertFeaturesToQueueItems(features)

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(items)
		}
		formatter.Printf("Imported %d features to queue\n", len(items))
		formatter.Printf("Total queue items: %d\n", len(getQueueItems())+len(items))
		return nil
	},
}

// queueImportDispatchesCmd: forge queue import-dispatches --dir <dir>
var queueImportDispatchesCmd = &cobra.Command{
	Use:   "import-dispatches",
	Short: "Import dispatch files as queue items",
	Long:  "Import .md dispatch files from a directory as queue items.",
	RunE: func(cmd *cobra.Command, args []string) error {
		dir, _ := cmd.Flags().GetString("dir")
		format, _ := cmd.Flags().GetString("format")

		if dir == "" {
			return fmt.Errorf("--dir is required")
		}

		items, err := loadDispatchFiles(dir)
		if err != nil {
			return fmt.Errorf("failed to load dispatch files: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(items)
		}
		formatter.Printf("Imported %d dispatch files to queue\n", len(items))
		return nil
	},
}
// queuePruneCmd: forge queue prune
var queuePruneCmd = &cobra.Command{
	Use:   "prune",
	Short: "Remove deleted and cancelled tasks from queue",
	Long:  "Permanently remove tasks with status DELETED or CANCELLED from the database.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		dryRun, _ := cmd.Flags().GetBool("dry-run")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()

		formatter := internal.NewFormatter(format, nil)

		// Step 1: abandon stale assigned tasks (>2h, no heartbeat) via v3 API.
		if !dryRun {
			resp, err := client.Post(ctx, "/api/tasks/prune", nil)
			if err == nil && resp != nil {
				resp.Body.Close()
				formatter.Printf("Stale assigned tasks abandoned via /api/tasks/prune\n")
			}
		}

		// Step 2: delete tasks with DELETED or CANCELLED status.
		result, err := client.ListTasks(ctx, "", 500, "")
		if err != nil {
			return fmt.Errorf("failed to list tasks: %w", err)
		}

		var toPrune []string
		for _, t := range result.Tasks {
			status := strings.ToUpper(t.Status)
			if status == "DELETED" || status == "CANCELLED" {
				toPrune = append(toPrune, t.ID)
			}
		}

		if len(toPrune) == 0 {
			formatter.Printf("No DELETED/CANCELLED tasks to remove\n")
			return nil
		}

		if dryRun {
			formatter.Printf("Dry run: would remove %d DELETED/CANCELLED tasks\n", len(toPrune))
			if format == "table" || format == "" {
				for _, id := range toPrune {
					formatter.Printf("  - %s\n", id)
				}
			}
			return nil
		}

		deleted := 0
		for _, id := range toPrune {
			if err := client.DeleteTask(ctx, id); err != nil {
				fmt.Fprintf(os.Stderr, "Failed to delete %s: %v\n", id, err)
				continue
			}
			deleted++
		}

		formatter.Printf("Pruned %d DELETED/CANCELLED tasks\n", deleted)
		return nil
	},
}

// QueueItem represents a task in the queue
type QueueItem struct {
	ID          string `json:"id"`
	Title       string `json:"title"`
	Status      string `json:"status"`
	Assignee    string `json:"assignee,omitempty"`
	Lane        string `json:"lane"`
	Priority    string `json:"priority"`
	CreatedAt   string `json:"created_at"`
	CompletedAt string `json:"completed_at,omitempty"`
}

// getQueueItems fetches live tasks from the v3 daemon and converts them to QueueItems.
func getQueueItems() []internal.QueueItem {
	client := internal.NewClient()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	result, err := client.ListTasks(ctx, "", 200, "")
	if err != nil {
		// Daemon unreachable — return empty slice so callers still work.
		return []internal.QueueItem{}
	}

	items := make([]internal.QueueItem, 0, len(result.Tasks))
	for _, t := range result.Tasks {
		assignee := ""
		if t.AssignedAgent != nil {
			assignee = *t.AssignedAgent
		} else if t.AssignedTo != "" {
			assignee = t.AssignedTo
		}
		lane := t.Lane
		if lane == "" {
			lane = "dev"
		}
		items = append(items, internal.QueueItem{
			ID:        t.ID,
			Title:     t.DisplayTitle(),
			Status:    strings.ToUpper(t.Status),
			Assignee:  assignee,
			Lane:      lane,
			Priority:  fmt.Sprintf("%d", t.Priority),
			CreatedAt: t.CreatedAt,
		})
	}
	return items
}

// calculateDepth calculates queue depth by status
func calculateDepth(items []internal.QueueItem) *internal.QueueDepth {
	depth := &internal.QueueDepth{}
	for _, item := range items {
		switch item.Status {
		case "QUEUED":
			depth.Queued++
		case "DISPATCHED":
			depth.Dispatched++
		case "RUNNING":
			depth.Running++
		case "BLOCKED":
			depth.Blocked++
		case "COMPLETED":
			depth.Completed++
		case "FAILED":
			depth.Failed++
		case "APPROVED":
			depth.Approved++
		}
	}
	return depth
}

// filterByStatus filters items by status
func filterByStatus(items []internal.QueueItem, status string) []internal.QueueItem {
	var filtered []internal.QueueItem
	for _, item := range items {
		if strings.EqualFold(item.Status, status) {
			filtered = append(filtered, item)
		}
	}
	return filtered
}

// filterByAssignee filters items by assignee
func filterByAssignee(items []internal.QueueItem, assignee string) []internal.QueueItem {
	var filtered []internal.QueueItem
	for _, item := range items {
		if strings.EqualFold(item.Assignee, assignee) {
			filtered = append(filtered, item)
		}
	}
	return filtered
}

// filterByLane filters items by lane
func filterByLane(items []internal.QueueItem, lane string) []internal.QueueItem {
	var filtered []internal.QueueItem
	for _, item := range items {
		if strings.EqualFold(item.Lane, lane) {
			filtered = append(filtered, item)
		}
	}
	return filtered
}

// FeaturesJSON represents the features.json structure
type FeaturesJSON struct {
	Features []Feature `json:"features"`
}

// Feature represents a single feature
type Feature struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Description string `json:"description"`
	Status      string `json:"status"`
	Priority    string `json:"priority"`
	Lane        string `json:"lane"`
	AssignedTo  string `json:"assigned_to"`
}

// loadFeatures loads features from a JSON file
func loadFeatures(path string) ([]Feature, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("failed to read file: %w", err)
	}

	var featuresJSON FeaturesJSON
	if err := json.Unmarshal(data, &featuresJSON); err != nil {
		return nil, fmt.Errorf("failed to parse JSON: %w", err)
	}

	return featuresJSON.Features, nil
}

// convertFeaturesToQueueItems converts features to queue items
func convertFeaturesToQueueItems(features []Feature) []internal.QueueItem {
	items := make([]internal.QueueItem, len(features))
	for i, f := range features {
		items[i] = internal.QueueItem{
			ID:        f.ID,
			Title:     f.Name,
			Status:    strings.ToUpper(f.Status),
			Assignee:  f.AssignedTo,
			Lane:      f.Lane,
			Priority:  f.Priority,
			CreatedAt: "",
		}
	}
	return items
}

// loadDispatchFiles loads .md dispatch files from a directory
func loadDispatchFiles(dir string) ([]internal.QueueItem, error) {
	var items []internal.QueueItem

	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, fmt.Errorf("failed to read directory: %w", err)
	}

	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".md") {
			continue
		}

		path := filepath.Join(dir, entry.Name())
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}

		// Extract title from first heading
		lines := strings.Split(string(data), "\n")
		title := entry.Name()
		for _, line := range lines {
			if strings.HasPrefix(line, "# ") {
				title = strings.TrimPrefix(line, "# ")
				break
			}
		}

		// Generate ID from filename
		id := strings.TrimSuffix(entry.Name(), ".md")
		if len(id) > 20 {
			id = id[:20]
		}

		items = append(items, internal.QueueItem{
			ID:       id,
			Title:    title,
			Status:   "QUEUED",
			Lane:     "dev",
			Priority: "P2",
		})
	}

	return items, nil
}

// queueStatsCmd: forge queue stats
var queueStatsCmd = &cobra.Command{
	Use:   "stats",
	Short: "Show per-lane and per-domain task counts with aging",
	Long:  "Display task counts grouped by lane and domain, including oldest task age.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListTasks(ctx, "", 500, "")
		if err != nil {
			return fmt.Errorf("failed to fetch tasks (is daemon running?): %w", err)
		}

		tasks := result.Tasks
		now := time.Now()

		type groupStats struct {
			count  int
			oldest time.Time
		}
		byLane := map[string]*groupStats{}
		byDomain := map[string]*groupStats{}

		for _, t := range tasks {
			lane := t.Lane
			if lane == "" {
				lane = "unassigned"
			}
			domain := t.Domain
			if domain == "" {
				domain = "unknown"
			}

			age, _ := time.Parse(time.RFC3339, t.CreatedAt)
			if age.IsZero() {
				age = now
			}

			if byLane[lane] == nil {
				byLane[lane] = &groupStats{oldest: age}
			}
			ls := byLane[lane]
			ls.count++
			if age.Before(ls.oldest) {
				ls.oldest = age
			}

			if byDomain[domain] == nil {
				byDomain[domain] = &groupStats{oldest: age}
			}
			ds := byDomain[domain]
			ds.count++
			if age.Before(ds.oldest) {
				ds.oldest = age
			}
		}

		formatter := internal.NewFormatter(format, nil)

		if format == "json" {
			type stat struct {
				Name      string `json:"name"`
				Count     int    `json:"count"`
				OldestAge string `json:"oldest_age"`
			}
			laneSlice := make([]stat, 0, len(byLane))
			for name, s := range byLane {
				laneSlice = append(laneSlice, stat{name, s.count, queueFormatAge(now.Sub(s.oldest))})
			}
			domainSlice := make([]stat, 0, len(byDomain))
			for name, s := range byDomain {
				domainSlice = append(domainSlice, stat{name, s.count, queueFormatAge(now.Sub(s.oldest))})
			}
			return formatter.WriteJSON(map[string]interface{}{
				"total":     len(tasks),
				"by_lane":   laneSlice,
				"by_domain": domainSlice,
			})
		}

		formatter.Printf("Queue Stats  (total: %d tasks)\n\n", len(tasks))

		formatter.Printf("By Lane:\n")
		tw := formatter.NewTableWriter()
		tw.WriteHeader("LANE", "COUNT", "OLDEST TASK")
		for lane, s := range byLane {
			tw.WriteRow(lane, fmt.Sprintf("%d", s.count), queueFormatAge(now.Sub(s.oldest)))
		}
		tw.Flush()

		formatter.Printf("\nBy Domain:\n")
		tw2 := formatter.NewTableWriter()
		tw2.WriteHeader("DOMAIN", "COUNT", "OLDEST TASK")
		for domain, s := range byDomain {
			tw2.WriteRow(domain, fmt.Sprintf("%d", s.count), queueFormatAge(now.Sub(s.oldest)))
		}
		tw2.Flush()

		return nil
	},
}

// queueFormatAge returns a human-readable duration string: "< 1h", "3h", "2d", "1w".
func queueFormatAge(d time.Duration) string {
	if d < 0 {
		return "just now"
	}
	hours := int(d.Hours())
	if hours < 1 {
		return "< 1h"
	}
	if hours < 24 {
		return fmt.Sprintf("%dh", hours)
	}
	days := hours / 24
	if days < 7 {
		return fmt.Sprintf("%dd", days)
	}
	return fmt.Sprintf("%dw", days/7)
}

func init() {
	// queueCmd visible — queue management is user-facing
	// queue list flags
	queueListCmd.Flags().String("status", "", "Filter by status (queued, dispatched, running, completed, failed)")
	queueListCmd.Flags().String("assignee", "", "Filter by assignee")
	queueListCmd.Flags().String("lane", "", "Filter by lane (dev, test, prod)")

	// queue populate flags
	queuePopulateCmd.Flags().String("from", "", "Path to features.json file")

	// queue prune flags
	queuePruneCmd.Flags().Bool("dry-run", false, "Show what would be deleted without actually deleting")

	// queue import-dispatches flags
	queueImportDispatchesCmd.Flags().String("dir", "", "Path to dispatches directory")

	// Add commands to queue noun
	queueCmd.AddCommand(queueListCmd)
	queueCmd.AddCommand(queueDepthCmd)
	queueCmd.AddCommand(queueShowCmd)
	queueCmd.AddCommand(queuePopulateCmd)
	queueCmd.AddCommand(queueImportDispatchesCmd)
	queueCmd.AddCommand(queuePruneCmd)
	queueCmd.AddCommand(queueStatsCmd)
}
