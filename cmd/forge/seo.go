// seo.go — forge seo: SEO readiness audit for landing pages
package main

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"text/tabwriter"

	"github.com/spf13/cobra"
)

// seoCmd is the top-level `forge seo` command.
var seoCmd = &cobra.Command{
	Use:   "seo",
	Short: "SEO tools for portfolio landing pages",
	Long: `SEO tools for auditing and improving portfolio landing page SEO readiness.

Examples:
  # Audit a domain's landing page
  forge seo audit codeswiftr-com`,
	Run: func(cmd *cobra.Command, args []string) {
		cmd.Help()
	},
}

// seoAuditCmd is `forge seo audit <domain>`.
var seoAuditCmd = &cobra.Command{
	Use:   "audit <domain>",
	Short: "Audit SEO readiness of a domain's landing page",
	Long: `Audit the SEO readiness of a domain's landing page directory.

The command looks for the landing page in apps/{domain}-landing/ and checks:
  - index.html existence and meta tags (title, description, OG tags, JSON-LD)
  - sitemap.xml and robots.txt presence and validity
  - Blog post coverage and missing frontmatter descriptions

Arguments:
  <domain>   Domain key, e.g. codeswiftr-com

Examples:
  forge seo audit codeswiftr-com
  forge seo audit venturecanvas-io`,
	Args: cobra.ExactArgs(1),
	RunE: seoAuditCmdImpl,
}

func init() {
	seoCmd.AddCommand(seoAuditCmd)
}

// seoCheckResult holds the result of a single SEO check.
type seoCheckResult struct {
	Check   string
	Status  string // PASS, FAIL, WARN, SKIP
	Details string
}

// seoAuditCmdImpl implements `forge seo audit <domain>`.
func seoAuditCmdImpl(cmd *cobra.Command, args []string) error {
	domain := args[0]
	root := forgeRootDir()
	if root == "" {
		root = "."
	}

	// Locate the landing page directory.
	landingDir, err := findLandingDir(root, domain)
	if err != nil {
		return fmt.Errorf("cannot locate landing page for %q: %w\n  Recovery: verify apps/%s-landing/ or apps/%s/ exists under FORGE_ROOT", domain, err, domain, domain)
	}

	fmt.Printf("SEO Audit: %s\n", domain)
	fmt.Printf("Directory: %s\n", landingDir)
	fmt.Println()

	var results []seoCheckResult

	// --- File presence checks ---
	results = append(results, seoCheckFile(landingDir, "index.html"))
	results = append(results, seoCheckFile(landingDir, "sitemap.xml"))
	results = append(results, seoCheckFile(landingDir, "robots.txt"))

	// --- HTML content checks ---
	indexPath := filepath.Join(landingDir, "index.html")
	if checkFileExists(indexPath) {
		htmlBytes, readErr := os.ReadFile(indexPath)
		if readErr != nil {
			results = append(results, seoCheckResult{
				Check:   "index.html readable",
				Status:  "FAIL",
				Details: fmt.Sprintf("cannot read file: %v", readErr),
			})
		} else {
			html := string(htmlBytes)
			results = append(results, checkMetaTitle(html))
			results = append(results, checkMetaDescription(html))
			results = append(results, checkOGTags(html))
			results = append(results, checkJSONLD(html))
		}
	} else {
		// index.html missing — skip dependent checks
		for _, check := range []string{"Meta title", "Meta description", "OG tags", "JSON-LD schema"} {
			results = append(results, seoCheckResult{Check: check, Status: "SKIP", Details: "index.html not found"})
		}
	}

	// --- robots.txt validity ---
	robotsPath := filepath.Join(landingDir, "robots.txt")
	if checkFileExists(robotsPath) {
		results = append(results, checkRobotsAllowGoogle(robotsPath))
	} else {
		results = append(results, seoCheckResult{Check: "Robots allows Google", Status: "SKIP", Details: "robots.txt not found"})
	}

	// --- Blog posts ---
	blogResults := checkBlogPosts(landingDir)
	results = append(results, blogResults...)

	// --- Render table ---
	printSEOAuditTable(results)

	// Exit with non-zero if any FAIL exists.
	for _, r := range results {
		if r.Status == "FAIL" {
			return fmt.Errorf("SEO audit found failures — fix the FAIL items above to improve SEO readiness")
		}
	}
	return nil
}

// findLandingDir locates the landing page directory for a domain.
// Tries: apps/{domain}-landing/, apps/{domain}/, apps/{domain}-marketing/
// Also tries stripping common TLD suffixes (e.g. "codeswiftr-com" → "codeswiftr").
func findLandingDir(root, domain string) (string, error) {
	// Build base name candidates: the domain as-is plus TLD-stripped variants.
	bases := []string{domain}
	for _, suffix := range []string{"-com", "-io", "-co", "-net", "-org", "-app"} {
		if strings.HasSuffix(domain, suffix) {
			bases = append(bases, strings.TrimSuffix(domain, suffix))
		}
	}

	var candidates []string
	for _, base := range bases {
		candidates = append(candidates,
			filepath.Join(root, "apps", base+"-landing"),
			filepath.Join(root, "apps", base+"-marketing"),
			filepath.Join(root, "apps", base),
		)
	}

	for _, dir := range candidates {
		if info, err := os.Stat(dir); err == nil && info.IsDir() {
			return dir, nil
		}
	}
	return "", fmt.Errorf("no directory found (tried %s-landing, %s-marketing, %s under apps/)", domain, domain, domain)
}

// seoCheckFile checks whether a file exists in a directory and returns a check result.
func seoCheckFile(dir, filename string) seoCheckResult {
	path := filepath.Join(dir, filename)
	if checkFileExists(path) {
		return seoCheckResult{Check: filename + " exists", Status: "PASS", Details: path}
	}
	return seoCheckResult{
		Check:   filename + " exists",
		Status:  "FAIL",
		Details: fmt.Sprintf("not found at %s", path),
	}
}

// titleRe matches a <title>...</title> tag (case-insensitive, handles multiline).
var titleRe = regexp.MustCompile(`(?i)<title[^>]*>([\s\S]*?)</title>`)

// checkMetaTitle validates the <title> tag length (30-60 chars).
func checkMetaTitle(html string) seoCheckResult {
	m := titleRe.FindStringSubmatch(html)
	if m == nil {
		return seoCheckResult{Check: "Meta title", Status: "FAIL", Details: "<title> tag not found"}
	}
	title := strings.TrimSpace(m[1])
	n := len(title)
	switch {
	case n == 0:
		return seoCheckResult{Check: "Meta title", Status: "FAIL", Details: "<title> tag is empty"}
	case n < 30:
		return seoCheckResult{
			Check:   "Meta title",
			Status:  "WARN",
			Details: fmt.Sprintf("title too short (%d chars, want 30-60): %q", n, truncate(title, 60)),
		}
	case n > 60:
		return seoCheckResult{
			Check:   "Meta title",
			Status:  "WARN",
			Details: fmt.Sprintf("title too long (%d chars, want 30-60): %q", n, truncate(title, 60)),
		}
	default:
		return seoCheckResult{
			Check:   "Meta title",
			Status:  "PASS",
			Details: fmt.Sprintf("%d chars: %q", n, truncate(title, 60)),
		}
	}
}

// metaDescRe matches <meta name="description" content="..."> (case-insensitive).
var metaDescRe = regexp.MustCompile(`(?i)<meta[^>]+name=["']description["'][^>]+content=["']([^"']*)["']`)

// metaDescRe2 handles reversed attribute order: content before name.
var metaDescRe2 = regexp.MustCompile(`(?i)<meta[^>]+content=["']([^"']*)["'][^>]+name=["']description["']`)

// checkMetaDescription validates the meta description length (50-160 chars).
func checkMetaDescription(html string) seoCheckResult {
	desc := ""
	if m := metaDescRe.FindStringSubmatch(html); m != nil {
		desc = strings.TrimSpace(m[1])
	} else if m := metaDescRe2.FindStringSubmatch(html); m != nil {
		desc = strings.TrimSpace(m[1])
	} else {
		return seoCheckResult{Check: "Meta description", Status: "FAIL", Details: `<meta name="description"> not found`}
	}

	n := len(desc)
	switch {
	case n == 0:
		return seoCheckResult{Check: "Meta description", Status: "FAIL", Details: "meta description is empty"}
	case n < 50:
		return seoCheckResult{
			Check:   "Meta description",
			Status:  "WARN",
			Details: fmt.Sprintf("description too short (%d chars, want 50-160): %q", n, truncate(desc, 80)),
		}
	case n > 160:
		return seoCheckResult{
			Check:   "Meta description",
			Status:  "WARN",
			Details: fmt.Sprintf("description too long (%d chars, want 50-160): %q", n, truncate(desc, 80)),
		}
	default:
		return seoCheckResult{
			Check:   "Meta description",
			Status:  "PASS",
			Details: fmt.Sprintf("%d chars", n),
		}
	}
}

// ogPropertyRe returns a regex to extract an OG property value.
func ogPropertyRe(property string) *regexp.Regexp {
	// Matches: <meta property="og:title" content="..."> or reversed attribute order.
	return regexp.MustCompile(`(?i)<meta[^>]+property=["']` + regexp.QuoteMeta(property) + `["'][^>]+content=["']([^"']*)["']`)
}

// ogPropertyReReversed handles content before property.
func ogPropertyReReversed(property string) *regexp.Regexp {
	return regexp.MustCompile(`(?i)<meta[^>]+content=["']([^"']*)["'][^>]+property=["']` + regexp.QuoteMeta(property) + `["']`)
}

// checkOGTags checks for og:title, og:description, and og:url.
func checkOGTags(html string) seoCheckResult {
	required := []string{"og:title", "og:description", "og:url"}
	var missing []string

	for _, prop := range required {
		re := ogPropertyRe(prop)
		reRev := ogPropertyReReversed(prop)
		if re.FindStringSubmatch(html) == nil && reRev.FindStringSubmatch(html) == nil {
			missing = append(missing, prop)
		}
	}

	if len(missing) == 0 {
		return seoCheckResult{Check: "OG tags", Status: "PASS", Details: "og:title, og:description, og:url present"}
	}
	return seoCheckResult{
		Check:   "OG tags",
		Status:  "FAIL",
		Details: fmt.Sprintf("missing: %s", strings.Join(missing, ", ")),
	}
}

// jsonldRe detects a JSON-LD script block.
var jsonldRe = regexp.MustCompile(`(?i)<script[^>]+type=["']application/ld\+json["']`)

// checkJSONLD checks whether a JSON-LD schema block is present.
func checkJSONLD(html string) seoCheckResult {
	if jsonldRe.MatchString(html) {
		return seoCheckResult{Check: "JSON-LD schema", Status: "PASS", Details: `<script type="application/ld+json"> found`}
	}
	return seoCheckResult{
		Check:   "JSON-LD schema",
		Status:  "FAIL",
		Details: `no <script type="application/ld+json"> found`,
	}
}

// checkRobotsAllowGoogle parses robots.txt and verifies Googlebot is allowed.
// Passes if "User-agent: *" + "Allow: /" or "User-agent: Googlebot" + "Allow: /" is found.
func checkRobotsAllowGoogle(robotsPath string) seoCheckResult {
	f, err := os.Open(robotsPath)
	if err != nil {
		return seoCheckResult{Check: "Robots allows Google", Status: "FAIL", Details: fmt.Sprintf("cannot open robots.txt: %v", err)}
	}
	defer f.Close()

	type block struct {
		agents []string
		allows []string
		denies []string
	}

	var blocks []block
	var current *block

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		val := strings.TrimSpace(parts[1])

		switch strings.ToLower(key) {
		case "user-agent":
			if current == nil || len(current.agents) > 0 && (len(current.allows) > 0 || len(current.denies) > 0) {
				// Start a new block
				blocks = append(blocks, block{})
				current = &blocks[len(blocks)-1]
			}
			current.agents = append(current.agents, val)
		case "allow":
			if current != nil {
				current.allows = append(current.allows, val)
			}
		case "disallow":
			if current != nil {
				current.denies = append(current.denies, val)
			}
		}
	}

	// Check if any block applies to Googlebot (or * wildcard) and allows /
	for _, b := range blocks {
		for _, agent := range b.agents {
			if agent == "*" || strings.EqualFold(agent, "Googlebot") {
				// Check for explicit Allow: / or empty Disallow (which means allow all)
				hasAllowRoot := false
				hasDisallowRoot := false
				for _, a := range b.allows {
					if a == "/" {
						hasAllowRoot = true
					}
				}
				for _, d := range b.denies {
					if d == "/" {
						hasDisallowRoot = true
					}
				}
				if hasAllowRoot && !hasDisallowRoot {
					return seoCheckResult{
						Check:   "Robots allows Google",
						Status:  "PASS",
						Details: fmt.Sprintf("User-agent: %s with Allow: /", agent),
					}
				}
				// Empty Disallow also means allowed
				if !hasDisallowRoot && len(b.denies) == 0 {
					return seoCheckResult{
						Check:   "Robots allows Google",
						Status:  "PASS",
						Details: fmt.Sprintf("User-agent: %s with no Disallow (implicitly allowed)", agent),
					}
				}
				if hasDisallowRoot {
					return seoCheckResult{
						Check:   "Robots allows Google",
						Status:  "FAIL",
						Details: fmt.Sprintf("User-agent: %s has Disallow: /", agent),
					}
				}
			}
		}
	}

	// If no matching block found, default is allow — WARN to prompt adding explicit rules
	return seoCheckResult{
		Check:   "Robots allows Google",
		Status:  "WARN",
		Details: "no User-agent: Googlebot or User-agent: * block found; add explicit rules",
	}
}

// checkBlogPosts checks for a blog directory, counts posts, and reports missing descriptions.
func checkBlogPosts(landingDir string) []seoCheckResult {
	// Common blog directory candidates
	blogDirs := []string{
		filepath.Join(landingDir, "blog"),
		filepath.Join(landingDir, "posts"),
		filepath.Join(landingDir, "content", "blog"),
		filepath.Join(landingDir, "src", "blog"),
		filepath.Join(landingDir, "src", "content", "blog"),
	}

	blogDir := ""
	for _, d := range blogDirs {
		if info, err := os.Stat(d); err == nil && info.IsDir() {
			blogDir = d
			break
		}
	}

	if blogDir == "" {
		return []seoCheckResult{
			{Check: "Blog posts", Status: "SKIP", Details: "no blog/ or posts/ directory found"},
		}
	}

	// Count markdown/HTML files in the blog directory (non-recursive top-level).
	entries, err := os.ReadDir(blogDir)
	if err != nil {
		return []seoCheckResult{
			{Check: "Blog posts", Status: "FAIL", Details: fmt.Sprintf("cannot read blog dir: %v", err)},
		}
	}

	var postFiles []string
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		name := strings.ToLower(e.Name())
		if strings.HasSuffix(name, ".md") || strings.HasSuffix(name, ".mdx") ||
			strings.HasSuffix(name, ".html") || strings.HasSuffix(name, ".htm") {
			postFiles = append(postFiles, filepath.Join(blogDir, e.Name()))
		}
	}

	var results []seoCheckResult

	// Blog post count check
	if len(postFiles) == 0 {
		results = append(results, seoCheckResult{
			Check:   "Blog posts count",
			Status:  "FAIL",
			Details: fmt.Sprintf("no blog posts found in %s", blogDir),
		})
	} else {
		results = append(results, seoCheckResult{
			Check:   "Blog posts count",
			Status:  "PASS",
			Details: fmt.Sprintf("%d post(s) found in %s", len(postFiles), blogDir),
		})
	}

	// Check frontmatter descriptions in markdown files
	missingDesc := checkBlogFrontmatterDescriptions(postFiles)
	if missingDesc == 0 {
		results = append(results, seoCheckResult{
			Check:   "Blog descriptions",
			Status:  "PASS",
			Details: "all posts have description in frontmatter",
		})
	} else {
		results = append(results, seoCheckResult{
			Check:   "Blog descriptions",
			Status:  "WARN",
			Details: fmt.Sprintf("%d post(s) missing description in frontmatter", missingDesc),
		})
	}

	return results
}

// frontmatterDescRe matches description in YAML frontmatter.
var frontmatterDescRe = regexp.MustCompile(`(?im)^description\s*:\s*.+$`)

// checkBlogFrontmatterDescriptions counts markdown files missing a description field.
func checkBlogFrontmatterDescriptions(files []string) int {
	missing := 0
	for _, path := range files {
		if !strings.HasSuffix(strings.ToLower(path), ".md") && !strings.HasSuffix(strings.ToLower(path), ".mdx") {
			continue // only check markdown
		}
		content, err := os.ReadFile(path)
		if err != nil {
			missing++
			continue
		}
		// Check for YAML frontmatter (starts with ---)
		text := string(content)
		if !strings.HasPrefix(strings.TrimSpace(text), "---") {
			missing++ // no frontmatter at all
			continue
		}
		if !frontmatterDescRe.MatchString(text) {
			missing++
		}
	}
	return missing
}

// printSEOAuditTable renders the SEO audit results as a table.
func printSEOAuditTable(results []seoCheckResult) {
	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "CHECK\tSTATUS\tDETAILS")
	fmt.Fprintln(w, "-----\t------\t-------")
	for _, r := range results {
		fmt.Fprintf(w, "%s\t%s\t%s\n", r.Check, r.Status, r.Details)
	}
	w.Flush()

	// Summary line
	pass, fail, warn, skip := 0, 0, 0, 0
	for _, r := range results {
		switch r.Status {
		case "PASS":
			pass++
		case "FAIL":
			fail++
		case "WARN":
			warn++
		case "SKIP":
			skip++
		}
	}
	fmt.Println()
	fmt.Printf("Summary: %d PASS  %d FAIL  %d WARN  %d SKIP\n", pass, fail, warn, skip)
}

