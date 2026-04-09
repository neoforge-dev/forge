package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"sort"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/spf13/cobra"
)

const kvNamespaceID = "866671c3955c4081935c6fd708acc5db"

// leadsCmd is the top-level "forge leads" noun for lead capture data.
var leadsCmd = &cobra.Command{
	Use:   "leads",
	Short: "Query lead capture data from Cloudflare KV",
	Long: `Query and manage leads captured by the lead-capture Cloudflare Worker.

Requires wrangler CLI authenticated with Cloudflare.

Examples:
  forge leads list                    # all leads
  forge leads list --domain babybit.es
  forge leads count                   # total + per-domain counts
  forge leads get <email>             # find a specific lead`,
	RunE: func(cmd *cobra.Command, args []string) error {
		return leadsListRun(cmd, args)
	},
}

// ── forge leads list ────────────────────────────────────────────────────────

var leadsListCmd = &cobra.Command{
	Use:   "list",
	Short: "List captured leads",
	Long: `List all leads from Cloudflare KV, optionally filtered by domain.

Examples:
  forge leads list
  forge leads list --domain codeswiftr.com
  forge leads list --format json`,
	RunE: leadsListRun,
}

func leadsListRun(cmd *cobra.Command, args []string) error {
	domain, _ := cmd.Flags().GetString("domain")
	format, _ := cmd.Flags().GetString("format")

	keys, err := kvListKeys("")
	if err != nil {
		return err
	}

	// Filter to data keys only (skip dedup: prefix)
	var dataKeys []kvKey
	for _, k := range keys {
		if strings.HasPrefix(k.Name, "dedup:") {
			continue
		}
		if domain != "" && !strings.HasPrefix(k.Name, domain+":") {
			continue
		}
		dataKeys = append(dataKeys, k)
	}

	if len(dataKeys) == 0 {
		if domain != "" {
			fmt.Printf("No leads found for domain: %s\n", domain)
		} else {
			fmt.Println("No leads captured yet.")
		}
		return nil
	}

	// Fetch each lead's value
	type leadRecord struct {
		Email   string `json:"email"`
		Name    *string `json:"name"`
		Domain  string `json:"domain"`
		Type    string `json:"type"`
		Product *string `json:"product"`
		Source  string `json:"source"`
		Country *string `json:"country"`
		Ts      string `json:"ts"`
	}

	var leads []leadRecord
	for _, k := range dataKeys {
		val, err := kvGetKey(k.Name)
		if err != nil {
			continue
		}
		var l leadRecord
		if err := json.Unmarshal([]byte(val), &l); err != nil {
			continue
		}
		leads = append(leads, l)
	}

	// Sort by timestamp descending (newest first)
	sort.Slice(leads, func(i, j int) bool {
		return leads[i].Ts > leads[j].Ts
	})

	if format == "json" {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(leads)
	}

	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "EMAIL\tDOMAIN\tTYPE\tCOUNTRY\tCAPTURED")
	for _, l := range leads {
		country := "-"
		if l.Country != nil {
			country = *l.Country
		}
		captured := formatTime(l.Ts)
		fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\n",
			l.Email, l.Domain, l.Type, country, captured)
	}
	return w.Flush()
}

// ── forge leads count ───────────────────────────────────────────────────────

var leadsCountCmd = &cobra.Command{
	Use:   "count",
	Short: "Count leads by domain",
	Long: `Show total lead count and breakdown by domain.

Examples:
  forge leads count
  forge leads count --format json`,
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		keys, err := kvListKeys("")
		if err != nil {
			return err
		}

		counts := make(map[string]int)
		total := 0
		for _, k := range keys {
			if strings.HasPrefix(k.Name, "dedup:") {
				continue
			}
			total++
			parts := strings.SplitN(k.Name, ":", 2)
			if len(parts) > 0 {
				counts[parts[0]]++
			}
		}

		if format == "json" {
			result := map[string]interface{}{
				"total":   total,
				"domains": counts,
			}
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(result)
		}

		fmt.Printf("Total leads: %d\n\n", total)
		if total == 0 {
			return nil
		}

		// Sort domains by count descending
		type dc struct {
			domain string
			count  int
		}
		var sorted []dc
		for d, c := range counts {
			sorted = append(sorted, dc{d, c})
		}
		sort.Slice(sorted, func(i, j int) bool {
			return sorted[i].count > sorted[j].count
		})

		w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
		fmt.Fprintln(w, "DOMAIN\tLEADS")
		for _, s := range sorted {
			fmt.Fprintf(w, "%s\t%d\n", s.domain, s.count)
		}
		return w.Flush()
	},
}

// ── forge leads get ─────────────────────────────────────────────────────────

var leadsGetCmd = &cobra.Command{
	Use:   "get <email>",
	Short: "Find a lead by email address",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		email := strings.ToLower(strings.TrimSpace(args[0]))
		format, _ := cmd.Flags().GetString("format")

		keys, err := kvListKeys("")
		if err != nil {
			return err
		}

		// Search for keys containing the email
		// Worker sanitizes: email.replace(/[^a-z0-9]/g, '_')
		emailSlug := sanitizeEmail(email)

		var matches []string
		for _, k := range keys {
			if strings.HasPrefix(k.Name, "dedup:") {
				continue
			}
			if strings.Contains(k.Name, emailSlug) {
				matches = append(matches, k.Name)
			}
		}

		if len(matches) == 0 {
			fmt.Printf("No lead found for: %s\n", email)
			return nil
		}

		var results []json.RawMessage
		for _, key := range matches {
			val, err := kvGetKey(key)
			if err != nil {
				continue
			}
			results = append(results, json.RawMessage(val))
		}

		if format == "json" {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(results)
		}

		for _, raw := range results {
			var m map[string]interface{}
			json.Unmarshal(raw, &m)
			for k, v := range m {
				if v == nil {
					continue
				}
				fmt.Printf("  %-12s %v\n", k+":", v)
			}
			fmt.Println()
		}
		return nil
	},
}

// ── forge leads delete ──────────────────────────────────────────────────────

var leadsDeleteCmd = &cobra.Command{
	Use:   "delete <email>",
	Short: "Delete a test lead by email (GDPR / cleanup)",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		email := strings.ToLower(strings.TrimSpace(args[0]))

		keys, err := kvListKeys("")
		if err != nil {
			return err
		}

		emailSlug := sanitizeEmail(email)

		var toDelete []string
		for _, k := range keys {
			if strings.Contains(k.Name, emailSlug) || strings.HasSuffix(k.Name, email) {
				toDelete = append(toDelete, k.Name)
			}
		}

		if len(toDelete) == 0 {
			fmt.Printf("No keys found for: %s\n", email)
			return nil
		}

		for _, key := range toDelete {
			if err := kvDeleteKey(key); err != nil {
				fmt.Fprintf(os.Stderr, "  failed to delete %s: %v\n", key, err)
			} else {
				fmt.Printf("  deleted: %s\n", key)
			}
		}
		fmt.Printf("Removed %d key(s) for %s\n", len(toDelete), email)
		return nil
	},
}

// ── KV helpers (shells out to wrangler) ─────────────────────────────────────

type kvKey struct {
	Name       string `json:"name"`
	Expiration int64  `json:"expiration,omitempty"`
}

func kvListKeys(prefix string) ([]kvKey, error) {
	args := []string{"kv", "key", "list",
		"--namespace-id", kvNamespaceID,
		"--remote",
	}
	if prefix != "" {
		args = append(args, "--prefix", prefix)
	}

	out, err := exec.Command("wrangler", args...).Output()
	if err != nil {
		return nil, fmt.Errorf("wrangler kv list failed: %w\n  Check: wrangler whoami", err)
	}

	var keys []kvKey
	if err := json.Unmarshal(out, &keys); err != nil {
		return nil, fmt.Errorf("parse kv keys: %w", err)
	}
	return keys, nil
}

func kvGetKey(key string) (string, error) {
	out, err := exec.Command("wrangler", "kv", "key", "get",
		"--namespace-id", kvNamespaceID,
		"--remote",
		key,
	).Output()
	if err != nil {
		return "", fmt.Errorf("wrangler kv get failed for %s: %w", key, err)
	}
	return string(out), nil
}

func kvDeleteKey(key string) error {
	cmd := exec.Command("wrangler", "kv", "key", "delete",
		"--namespace-id", kvNamespaceID,
		"--remote",
		key,
	)
	cmd.Stdin = strings.NewReader("y\n")
	return cmd.Run()
}

// sanitizeEmail matches the worker's JS: email.replace(/[^a-z0-9]/g, '_')
func sanitizeEmail(email string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(email) {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
			b.WriteRune(r)
		} else {
			b.WriteRune('_')
		}
	}
	return b.String()
}

func formatTime(ts string) string {
	t, err := time.Parse(time.RFC3339, ts)
	if err != nil {
		return ts
	}
	return t.Format("2006-01-02 15:04")
}

// ── init ────────────────────────────────────────────────────────────────────

func init() {
	leadsCmd.AddCommand(leadsListCmd)
	leadsCmd.AddCommand(leadsCountCmd)
	leadsCmd.AddCommand(leadsGetCmd)
	leadsCmd.AddCommand(leadsDeleteCmd)

	// Shared flags
	leadsCmd.PersistentFlags().String("format", "table", "Output format: table or json")

	// List-specific flags
	leadsCmd.Flags().String("domain", "", "Filter by domain")
	leadsListCmd.Flags().String("domain", "", "Filter by domain")
}
