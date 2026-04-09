// notion.go — forge notion: sync portfolio/task data to Notion via REST API
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

const notionAPIURL = "https://api.notion.com/v1"
const notionVersion = "2022-06-28"

// notionClient wraps the Notion REST API using stdlib net/http only.
type notionClient struct {
	apiKey     string
	databaseID string
	httpClient *http.Client
}

// newNotionClient constructs a client from environment variables.
// Returns an error with recovery hint if NOTION_API_KEY is missing.
func newNotionClient(databaseOverride string) (*notionClient, error) {
	key := os.Getenv("NOTION_API_KEY")
	if key == "" {
		return nil, fmt.Errorf("NOTION_API_KEY not set\n  Fix: export NOTION_API_KEY=ntn_xxx")
	}

	dbID := databaseOverride
	if dbID == "" {
		dbID = os.Getenv("NOTION_DATABASE_ID")
	}

	return &notionClient{
		apiKey:     key,
		databaseID: dbID,
		httpClient: &http.Client{Timeout: 15 * time.Second},
	}, nil
}

// request executes an authenticated Notion API call.
func (nc *notionClient) request(method, path string, body interface{}) (*http.Response, error) {
	var bodyReader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("marshal request body: %w", err)
		}
		bodyReader = bytes.NewReader(data)
	}

	req, err := http.NewRequest(method, notionAPIURL+path, bodyReader)
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+nc.apiKey)
	req.Header.Set("Notion-Version", notionVersion)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := nc.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("notion API request failed: %w\n  Check network and NOTION_API_KEY validity", err)
	}
	return resp, nil
}

// checkResponse reads a Notion API response, returning an error for non-2xx status.
func checkResponse(resp *http.Response) ([]byte, error) {
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response body: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		// Try to surface the Notion error message.
		var apiErr struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		}
		_ = json.Unmarshal(body, &apiErr)
		if apiErr.Message != "" {
			return nil, fmt.Errorf("notion API error %d (%s): %s\n  Fix: check NOTION_API_KEY permissions and NOTION_DATABASE_ID", resp.StatusCode, apiErr.Code, apiErr.Message)
		}
		return nil, fmt.Errorf("notion API returned HTTP %d\n  Fix: check NOTION_API_KEY permissions and NOTION_DATABASE_ID", resp.StatusCode)
	}
	return body, nil
}

// ── Top-level notion command ─────────────────────────────────────────────────

var notionCmd = &cobra.Command{
	Use:   "notion",
	Short: "Sync portfolio data to Notion",
	Long: `Sync FORGE portfolio and task data to a Notion database via the Notion REST API.

Required environment variables:
  NOTION_API_KEY        Notion integration token (ntn_xxx)
  NOTION_DATABASE_ID    Target Notion database ID

Examples:
  forge notion status                   # Verify connection and database access
  forge notion sync --type portfolio    # Push domain/product status to Notion
  forge notion sync --type tasks        # Push recent completed tasks to Notion
  forge notion sync --type portfolio --dry-run  # Print JSON without sending`,
	Run: func(cmd *cobra.Command, args []string) {
		cmd.Help()
	},
}

// ── forge notion status ──────────────────────────────────────────────────────

var notionStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Verify Notion API connection and database access",
	Long: `Verify that NOTION_API_KEY is valid and the configured database is accessible.

Checks:
  1. GET /v1/users/me  — confirms API key is valid
  2. GET /v1/databases/{NOTION_DATABASE_ID}  — confirms database access

Examples:
  forge notion status
  NOTION_DATABASE_ID=abc forge notion status`,
	RunE: runNotionStatus,
}

func runNotionStatus(cmd *cobra.Command, _ []string) error {
	dbOverride, _ := cmd.Flags().GetString("database")
	nc, err := newNotionClient(dbOverride)
	if err != nil {
		return err
	}

	// 1. Verify API key via /users/me
	resp, err := nc.request("GET", "/users/me", nil)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := checkResponse(resp)
	if err != nil {
		return fmt.Errorf("API key check failed: %w", err)
	}

	var me struct {
		Name string `json:"name"`
		Type string `json:"type"`
	}
	_ = json.Unmarshal(body, &me)
	if me.Name != "" {
		fmt.Printf("Connected to Notion as: %s (%s)\n", me.Name, me.Type)
	} else {
		fmt.Println("Connected to Notion. (API key valid)")
	}

	// 2. Verify database access
	if nc.databaseID == "" {
		fmt.Println("NOTION_DATABASE_ID not set — skipping database check.")
		fmt.Println("  Set it: export NOTION_DATABASE_ID=<your-database-id>")
		return nil
	}

	dbResp, err := nc.request("GET", "/databases/"+nc.databaseID, nil)
	if err != nil {
		return err
	}
	defer dbResp.Body.Close()
	dbBody, err := checkResponse(dbResp)
	if err != nil {
		return fmt.Errorf("database access check failed: %w", err)
	}

	var db struct {
		Title []struct {
			PlainText string `json:"plain_text"`
		} `json:"title"`
	}
	_ = json.Unmarshal(dbBody, &db)

	dbTitle := nc.databaseID
	if len(db.Title) > 0 && db.Title[0].PlainText != "" {
		dbTitle = db.Title[0].PlainText
	}
	fmt.Printf("Database: %s (ID: %s)\n", dbTitle, nc.databaseID)
	fmt.Println("Status: OK — ready to sync.")
	return nil
}

// ── forge notion sync ────────────────────────────────────────────────────────

var notionSyncCmd = &cobra.Command{
	Use:   "sync",
	Short: "Sync portfolio or task data to Notion",
	Long: `Push FORGE data to a Notion database.

Sync types:
  portfolio  Push domain/product status (name, domain, stage, MRR, next gate)
  tasks      Push recent completed tasks (title, status, domain, agent)

Examples:
  forge notion sync --type portfolio
  forge notion sync --type tasks
  forge notion sync --type portfolio --dry-run
  forge notion sync --type portfolio --database abc123`,
	RunE: runNotionSync,
}

func runNotionSync(cmd *cobra.Command, _ []string) error {
	syncType, _ := cmd.Flags().GetString("type")
	dryRun, _ := cmd.Flags().GetBool("dry-run")
	dbOverride, _ := cmd.Flags().GetString("database")

	if syncType == "" {
		return fmt.Errorf("--type is required\n  Use: forge notion sync --type portfolio|tasks")
	}
	if syncType != "portfolio" && syncType != "tasks" {
		return fmt.Errorf("unknown --type %q: must be portfolio or tasks\n  Use: forge notion sync --type portfolio|tasks", syncType)
	}

	nc, err := newNotionClient(dbOverride)
	if err != nil {
		return err
	}

	if !dryRun && nc.databaseID == "" {
		return fmt.Errorf("NOTION_DATABASE_ID not set\n  Fix: export NOTION_DATABASE_ID=<your-database-id>  (or use --database flag)")
	}

	switch syncType {
	case "portfolio":
		return runNotionSyncPortfolio(cmd, nc, dryRun)
	case "tasks":
		return runNotionSyncTasks(cmd, nc, dryRun)
	}
	return nil
}

// ── portfolio sync ───────────────────────────────────────────────────────────

// notionPage represents a single page creation request to the Notion API.
type notionPage struct {
	Parent     notionParent            `json:"parent"`
	Properties map[string]interface{}  `json:"properties"`
}

type notionParent struct {
	DatabaseID string `json:"database_id"`
}

func notionTitle(text string) map[string]interface{} {
	return map[string]interface{}{
		"title": []map[string]interface{}{
			{"text": map[string]interface{}{"content": text}},
		},
	}
}

func notionRichText(text string) map[string]interface{} {
	return map[string]interface{}{
		"rich_text": []map[string]interface{}{
			{"text": map[string]interface{}{"content": text}},
		},
	}
}

func notionSelect(option string) map[string]interface{} {
	if option == "" {
		return map[string]interface{}{"select": nil}
	}
	return map[string]interface{}{
		"select": map[string]interface{}{"name": option},
	}
}

func notionNumber(n int) map[string]interface{} {
	return map[string]interface{}{"number": n}
}

func runNotionSyncPortfolio(cmd *cobra.Command, nc *notionClient, dryRun bool) error {
	apiURL := internal.ResolveControlPlaneURL()
	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(apiURL))

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/portfolio")
	if err != nil {
		return fmt.Errorf("fetch portfolio from daemon: %w\n  Recovery: check 'forge daemon status'", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read portfolio response: %w", err)
	}

	// The /portfolio endpoint returns a PortfolioState-shaped object.
	var portfolio struct {
		Products []struct {
			Key        string `json:"key"`
			Name       string `json:"name"`
			Domain     string `json:"domain"`
			Stage      string `json:"stage"`
			Status     string `json:"status"`
			CurrentMRR int    `json:"current_mrr"`
			TargetMRR  int    `json:"target_mrr"`
			NextGate   string `json:"next_gate"`
			NextAction string `json:"next_action"`
		} `json:"products"`
	}
	if err := json.Unmarshal(body, &portfolio); err != nil {
		return fmt.Errorf("parse portfolio response: %w", err)
	}

	if len(portfolio.Products) == 0 {
		fmt.Println("No products found in portfolio — nothing to sync.")
		return nil
	}

	fmt.Printf("Syncing %d products to Notion...\n", len(portfolio.Products))
	synced := 0
	for _, p := range portfolio.Products {
		page := notionPage{
			Parent: notionParent{DatabaseID: nc.databaseID},
			Properties: map[string]interface{}{
				"Name":       notionTitle(p.Name),
				"Domain":     notionRichText(p.Domain),
				"Stage":      notionSelect(p.Stage),
				"Status":     notionSelect(p.Status),
				"Current MRR": notionNumber(p.CurrentMRR),
				"Target MRR": notionNumber(p.TargetMRR),
				"Next Gate":  notionRichText(p.NextGate),
				"Next Action": notionRichText(p.NextAction),
				"Key":        notionRichText(p.Key),
			},
		}

		if dryRun {
			data, _ := json.MarshalIndent(page, "", "  ")
			fmt.Printf("[dry-run] product=%s\n%s\n\n", p.Key, string(data))
			synced++
			continue
		}

		if err := nc.createPage(page); err != nil {
			fmt.Fprintf(os.Stderr, "  failed to sync %s: %v\n", p.Key, err)
			continue
		}
		fmt.Printf("  synced: %s (%s)\n", p.Name, p.Domain)
		synced++
	}

	if !dryRun {
		fmt.Printf("Done. %d/%d products synced to Notion.\n", synced, len(portfolio.Products))
	}
	return nil
}

// ── tasks sync ───────────────────────────────────────────────────────────────

func runNotionSyncTasks(cmd *cobra.Command, nc *notionClient, dryRun bool) error {
	apiURL := internal.ResolveControlPlaneURL()
	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(apiURL))

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/tasks?limit=50&status=completed")
	if err != nil {
		return fmt.Errorf("fetch tasks from daemon: %w\n  Recovery: check 'forge daemon status'", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read tasks response: %w", err)
	}

	var result struct {
		Tasks []struct {
			ID      string `json:"id"`
			Title   string `json:"title"`
			Status  string `json:"status"`
			State   string `json:"state"`
			Domain  string `json:"domain"`
			AgentID string `json:"agent_id"`
		} `json:"tasks"`
		Count int `json:"count"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return fmt.Errorf("parse tasks response: %w", err)
	}

	if len(result.Tasks) == 0 {
		fmt.Println("No completed tasks found — nothing to sync.")
		return nil
	}

	fmt.Printf("Syncing %d tasks to Notion...\n", len(result.Tasks))
	synced := 0
	for _, t := range result.Tasks {
		status := t.Status
		if status == "" {
			status = t.State
		}

		page := notionPage{
			Parent: notionParent{DatabaseID: nc.databaseID},
			Properties: map[string]interface{}{
				"Name":    notionTitle(t.Title),
				"Task ID": notionRichText(t.ID),
				"Status":  notionSelect(status),
				"Domain":  notionRichText(t.Domain),
				"Agent":   notionRichText(t.AgentID),
			},
		}

		if dryRun {
			data, _ := json.MarshalIndent(page, "", "  ")
			fmt.Printf("[dry-run] task=%s\n%s\n\n", t.ID, string(data))
			synced++
			continue
		}

		if err := nc.createPage(page); err != nil {
			fmt.Fprintf(os.Stderr, "  failed to sync task %s: %v\n", t.ID, err)
			continue
		}
		fmt.Printf("  synced: %s [%s]\n", t.Title, t.ID)
		synced++
	}

	if !dryRun {
		fmt.Printf("Done. %d/%d tasks synced to Notion.\n", synced, len(result.Tasks))
	}
	return nil
}

// createPage sends a POST /v1/pages request to create a new Notion page.
func (nc *notionClient) createPage(page notionPage) error {
	resp, err := nc.request("POST", "/pages", page)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, err = checkResponse(resp)
	return err
}

// ── init ─────────────────────────────────────────────────────────────────────

func init() {
	// notion status flags
	notionStatusCmd.Flags().String("database", "", "Override NOTION_DATABASE_ID")

	// notion sync flags
	notionSyncCmd.Flags().String("type", "", "Sync type: portfolio or tasks (required)")
	notionSyncCmd.Flags().Bool("dry-run", false, "Print JSON payload without sending to Notion")
	notionSyncCmd.Flags().String("database", "", "Override NOTION_DATABASE_ID env var")

	notionCmd.AddCommand(notionStatusCmd)
	notionCmd.AddCommand(notionSyncCmd)
}
