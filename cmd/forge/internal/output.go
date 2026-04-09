// Package internal provides output formatting for the forge CLI.
package internal

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"text/tabwriter"
	"time"
)

// Format represents the output format.
type Format string

const (
	// FormatTable outputs data in a tabular format.
	FormatTable Format = "table"
	// FormatJSON outputs data as JSON.
	FormatJSON Format = "json"
	// FormatCSV outputs data as CSV.
	FormatCSV Format = "csv"
	// FormatQuiet suppresses output (only returns exit codes).
	FormatQuiet Format = "quiet"
)

// Formatter handles output formatting.
type Formatter struct {
	format Format
	writer io.Writer
}

// NewFormatter creates a new formatter.
func NewFormatter(format string, writer io.Writer) *Formatter {
	if writer == nil {
		writer = os.Stdout
	}

	f := Format(strings.ToLower(format))
	switch f {
	case FormatJSON, FormatCSV, FormatQuiet:
		// valid
	default:
		f = FormatTable
	}

	return &Formatter{
		format: f,
		writer: writer,
	}
}

// Format returns the current format.
func (f *Formatter) Format() Format {
	return f.format
}

// IsQuiet returns true if the format is quiet.
func (f *Formatter) IsQuiet() bool {
	return f.format == FormatQuiet
}

// WriteJSON writes data as JSON.
func (f *Formatter) WriteJSON(data interface{}) error {
	if f.format == FormatQuiet {
		return nil
	}

	encoder := json.NewEncoder(f.writer)
	encoder.SetIndent("", "  ")
	return encoder.Encode(data)
}

// WriteCSV writes data as CSV.
func (f *Formatter) WriteCSV(headers []string, rows [][]string) error {
	if f.format == FormatQuiet {
		return nil
	}

	w := csv.NewWriter(f.writer)
	if err := w.Write(headers); err != nil {
		return err
	}
	for _, row := range rows {
		if err := w.Write(row); err != nil {
			return err
		}
	}
	w.Flush()
	return w.Error()
}

// TableWriter provides a tabwriter for table output.
type TableWriter struct {
	writer *tabwriter.Writer
	format Format
}

// NewTableWriter creates a new table writer.
func (f *Formatter) NewTableWriter() *TableWriter {
	if f.format == FormatQuiet {
		return &TableWriter{format: FormatQuiet}
	}

	return &TableWriter{
		writer: tabwriter.NewWriter(f.writer, 0, 0, 2, ' ', 0),
		format: f.format,
	}
}

// WriteHeader writes the table header.
func (tw *TableWriter) WriteHeader(headers ...string) {
	if tw.format == FormatQuiet || tw.format == FormatJSON || tw.format == FormatCSV {
		return
	}
	fmt.Fprintln(tw.writer, strings.Join(headers, "\t"))
}

// WriteRow writes a table row.
func (tw *TableWriter) WriteRow(values ...string) {
	if tw.format == FormatQuiet {
		return
	}
	fmt.Fprintln(tw.writer, strings.Join(values, "\t"))
}

// Flush flushes the table writer.
func (tw *TableWriter) Flush() error {
	if tw.writer == nil {
		return nil
	}
	return tw.writer.Flush()
}

// Printf prints formatted text.
func (f *Formatter) Printf(format string, a ...interface{}) {
	if f.format != FormatQuiet {
		fmt.Fprintf(f.writer, format, a...)
	}
}

// Println prints a line.
func (f *Formatter) Println(a ...interface{}) {
	if f.format != FormatQuiet {
		fmt.Fprintln(f.writer, a...)
	}
}

// Print prints text.
func (f *Formatter) Print(a ...interface{}) {
	if f.format != FormatQuiet {
		fmt.Fprint(f.writer, a...)
	}
}

// Error prints an error message to stderr.
func (f *Formatter) Error(format string, a ...interface{}) {
	fmt.Fprintf(os.Stderr, "Error: "+format+"\n", a...)
}

// ==================== Task Formatting ====================

// FormatTasks formats tasks according to the output format.
func (f *Formatter) FormatTasks(tasks []Task) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(tasks)
	case FormatCSV:
		headers := []string{"ID", "Title", "Status", "Priority", "Description", "Assigned Agent"}
		rows := make([][]string, len(tasks))
		for i, t := range tasks {
			assignedAgent := "-"
			if t.AssignedAgent != nil {
				assignedAgent = *t.AssignedAgent
			} else if t.AssignedTo != "" {
				assignedAgent = t.AssignedTo
			}
			rows[i] = []string{
				t.ID,
				t.DisplayTitle(),
				t.Status,
				PriorityToLabel(t.Priority),
				t.Description,
				assignedAgent,
			}
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		for _, t := range tasks {
			fmt.Fprintf(f.writer, "%s\n", t.ID)
		}
		return nil
	default: // table
		tw := f.NewTableWriter()
		tw.WriteHeader("ID", "TITLE", "STATUS", "PRIORITY", "ASSIGNED")
		for _, t := range tasks {
			assignedAgent := "-"
			if t.AssignedAgent != nil {
				assignedAgent = *t.AssignedAgent
			} else if t.AssignedTo != "" {
				assignedAgent = t.AssignedTo
			}
			tw.WriteRow(
				truncate(t.ID, 20),
				truncate(t.DisplayTitle(), 30),
				truncate(t.Status, 12),
				truncate(PriorityToLabel(t.Priority), 10),
				assignedAgent,
			)
		}
		return tw.Flush()
	}
}

// FormatTask formats a single task.
func (f *Formatter) FormatTask(task *Task) error {
	switch f.format {
	case FormatJSON:
		// Raw JSON representation for --format json
		return f.WriteJSON(task)
	case FormatCSV, FormatQuiet:
		return nil
	default: // human-readable detail view
		assignedTo := "-"
		if task.AssignedAgent != nil {
			assignedTo = *task.AssignedAgent
		} else if task.AssignedTo != "" {
			assignedTo = task.AssignedTo
		}

		title := task.Title
		if title == "" {
			title = task.DisplayTitle()
		}

		f.Printf("ID:          %s\n", task.ID)
		if title != "" && title != "-" {
			f.Printf("Title:       %s\n", title)
		}
		if task.Status != "" {
			f.Printf("Status:      %s\n", task.Status)
		}
		f.Printf("Priority:    %s (%d)\n", PriorityToLabel(task.Priority), task.Priority)
		if task.Domain != "" {
			f.Printf("Domain:      %s\n", task.Domain)
		}
		if task.Project != "" {
			f.Printf("Project:     %s\n", task.Project)
		}
		if task.TaskType != "" {
			f.Printf("Type:        %s\n", task.TaskType)
		}
		if assignedTo != "" && assignedTo != "-" {
			f.Printf("Assigned To: %s\n", assignedTo)
		}
		if task.Lane != "" {
			f.Printf("Lane:        %s\n", task.Lane)
		}
		if task.CreatedAt != "" {
			f.Printf("Created:     %s\n", task.CreatedAt)
		}
		if task.UpdatedAt != "" {
			f.Printf("Updated:     %s\n", task.UpdatedAt)
		}
		if task.Description != "" {
			f.Printf("Description: %s\n", task.Description)
		}
		if task.SourceFile != nil {
			f.Printf("Source File: %s\n", *task.SourceFile)
		}
		if task.ResultPath != nil {
			f.Printf("Result Path: %s\n", *task.ResultPath)
		}
		if len(task.Metadata) > 0 {
			f.Printf("\nMetadata:\n")
			keys := make([]string, 0, len(task.Metadata))
			for k := range task.Metadata {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				f.Printf("  %s: %v\n", k, task.Metadata[k])
			}
		}
		return nil
	}
}

// ==================== Agent Formatting ====================

// FormatAgents formats agents according to the output format.
func (f *Formatter) FormatAgents(agents []Agent) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(agents)
	case FormatCSV:
		headers := []string{"ID", "Node", "Status", "WorkState", "Role", "Tier", "Context%", "Current Task"}
		rows := make([][]string, len(agents))
		for i, a := range agents {
			currentTask := "-"
			if a.CurrentTask != nil {
				currentTask = *a.CurrentTask
			}
			workState := a.WorkState
			if workState == "" {
				workState = "idle"
			}
			rows[i] = []string{
				a.ID,
				a.Node,
				a.Status,
				workState,
				a.Role,
				a.Tier,
				fmt.Sprintf("%.1f%%", a.ContextPct),
				currentTask,
			}
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		for _, a := range agents {
			fmt.Fprintf(f.writer, "%s\n", a.ID)
		}
		return nil
	default: // table
		tw := f.NewTableWriter()
		tw.WriteHeader("ID", "NODE", "STATUS", "WORK STATE", "ROLE", "TIER", "CONTEXT%", "CURRENT TASK")
		for _, a := range agents {
			currentTask := "-"
			if a.CurrentTask != nil {
				currentTask = *a.CurrentTask
			}
			workState := a.WorkState
			if workState == "" {
				workState = "idle"
			}
			// Subtle color hints: agent ID by model prefix, node column by node name.
			agentID := ColorModel(a.ID, truncate(a.ID, 20))
			node := ColorNode(a.Node, truncate(a.Node, 12))
			tw.WriteRow(
				agentID,
				node,
				truncate(a.Status, 10),
				truncate(workState, 10),
				truncate(a.Role, 15),
				truncate(a.Tier, 10),
				fmt.Sprintf("%.1f%%", a.ContextPct),
				truncate(currentTask, 20),
			)
		}
		return tw.Flush()
	}
}

// FormatAgentHealth formats agent health.
func (f *Formatter) FormatAgentHealth(health *AgentHealth) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(health)
	case FormatCSV, FormatQuiet:
		return nil
	default: // table
		currentTask := "-"
		if health.CurrentTask != nil {
			currentTask = *health.CurrentTask
		}
		workState := health.WorkState
		if workState == "" {
			workState = "idle"
		}
		f.Printf("Agent: %s\n", health.AgentID)
		f.Printf("  Node:         %s\n", health.Node)
		f.Printf("  Status:       %s\n", health.Status)
		f.Printf("  Work State:   %s\n", workState)
		f.Printf("  Context:      %.1f%%\n", health.ContextPct)
		f.Printf("  Last Seen:    %s\n", health.LastSeen.Format("2006-01-02 15:04:05"))
		f.Printf("  Connected At: %s\n", health.ConnectedAt.Format("2006-01-02 15:04:05"))
		f.Printf("  Current Task: %s\n", currentTask)
		if len(health.Capabilities) > 0 {
			f.Printf("  Capabilities: %s\n", strings.Join(health.Capabilities, ", "))
		}
		return nil
	}
}

// ==================== Config Formatting ====================

// FormatConfig formats configuration.
func (f *Formatter) FormatConfig(config *Config) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(config)
	case FormatCSV:
		headers := []string{"Key", "Value"}
		rows := [][]string{
			{"API URL", config.APIURL},
			{"Node ID", config.NodeID},
			{"Domain", config.Domain},
		}
		for k, v := range config.Settings {
			rows = append(rows, []string{k, v})
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		return nil
	default: // table
		f.Printf("Configuration:\n")
		f.Printf("  API URL:  %s\n", config.APIURL)
		f.Printf("  Node ID:  %s\n", config.NodeID)
		f.Printf("  Domain:   %s\n", config.Domain)
		if len(config.Settings) > 0 {
			f.Printf("  Settings:\n")
			for k, v := range config.Settings {
				f.Printf("    %s: %s\n", k, v)
			}
		}
		return nil
	}
}

// ==================== Daemon Formatting ====================

// FormatDaemonStatus formats daemon status.
func (f *Formatter) FormatDaemonStatus(status *DaemonStatus) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(status)
	case FormatQuiet:
		return nil
	default: // table
		f.Printf("Daemon: %s\n", status.Status)
		f.Printf("  Version:       %s\n", status.Version)
		if status.Uptime != "" {
			f.Printf("  Uptime:        %s\n", status.Uptime)
		}
		f.Printf("  Pending Tasks: %d\n", status.PendingTasks)
		return nil
	}
}

// ==================== Domain Formatting ====================

// FormatDomains formats domains according to the output format.
func (f *Formatter) FormatDomains(domains []Domain) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(domains)
	case FormatCSV:
		headers := []string{"Key", "Display Name", "Owner", "Frontend", "Products", "Active", "MRR", "Deploy Status"}
		rows := make([][]string, len(domains))
		for i, d := range domains {
			products := strings.Join(d.Products, ", ")
			if len(products) > 30 {
				products = products[:27] + "..."
			}
			rows[i] = []string{
				d.Key,
				d.DisplayName,
				d.Owner,
				d.FrontendTier,
				products,
				fmt.Sprintf("%t", d.Active),
				fmt.Sprintf("$%d", d.Business.CurrentMRR),
				d.Business.DeployStatus,
			}
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		for _, d := range domains {
			fmt.Fprintf(f.writer, "%s\n", d.Key)
		}
		return nil
	default: // table
		tw := f.NewTableWriter()
		tw.WriteHeader("KEY", "DISPLAY NAME", "OWNER", "PRODUCTS", "ACTIVE", "MRR", "DEPLOY")
		for _, d := range domains {
			products := strings.Join(d.Products, ", ")
			if len(products) > 20 {
				products = products[:17] + "..."
			}
			owner := d.Owner
			if owner != "" {
				owner = NodeColor(owner) + owner + ColorReset
			}
			tw.WriteRow(
				truncate(d.Key, 20),
				truncate(d.DisplayName, 16),
				owner,
				truncate(products, 20),
				fmt.Sprintf("%t", d.Active),
				fmt.Sprintf("$%d", d.Business.CurrentMRR),
				truncate(d.Business.DeployStatus, 12),
			)
		}
		return tw.Flush()
	}
}

// FormatDomainDetail formats a single domain.
func (f *Formatter) FormatDomainDetail(domain *Domain) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(domain)
	case FormatCSV, FormatQuiet:
		return nil
	default: // table
		f.Printf("Domain: %s\n", domain.Key)
		f.Printf("  Display Name:    %s\n", domain.DisplayName)
		if domain.Owner != "" {
			f.Printf("  Owner:           %s%s%s\n", NodeColor(domain.Owner), domain.Owner, ColorReset)
		}
		f.Printf("  Frontend:        %s\n", domain.FrontendTier)
		f.Printf("  Active:          %t\n", domain.Active)
		if len(domain.Compliance) > 0 {
			f.Printf("  Compliance:      %s\n", strings.Join(domain.Compliance, ", "))
		}
		if len(domain.Products) > 0 {
			f.Printf("  Products:        %s\n", strings.Join(domain.Products, ", "))
		}
		f.Printf("  Business:\n")
		f.Printf("    Deploy Status:  %s\n", domain.Business.DeployStatus)
		f.Printf("    Current MRR:   $%d\n", domain.Business.CurrentMRR)
		f.Printf("    Target MRR:    $%d\n", domain.Business.TargetMRR)
		f.Printf("    Stripe Live:   %t\n", domain.Business.StripeLive)
		if len(domain.Business.HumanActionsNeeded) > 0 {
			f.Printf("    Human Actions: %d needed\n", len(domain.Business.HumanActionsNeeded))
		}
		return nil
	}
}

// ==================== Project Formatting ====================

// FormatProjects formats projects according to the output format.
func (f *Formatter) FormatProjects(projects []Project) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(projects)
	case FormatCSV:
		headers := []string{"Key", "Name", "Domain", "Type", "Status", "Path"}
		rows := make([][]string, len(projects))
		for i, p := range projects {
			rows[i] = []string{
				p.Key,
				p.Name,
				p.Domain,
				p.Type,
				p.Status,
				p.Path,
			}
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		return nil
	default: // table
		tw := f.NewTableWriter()
		tw.WriteHeader("KEY", "NAME", "DOMAIN", "TYPE", "STATUS")
		for _, p := range projects {
			tw.WriteRow(
				truncate(p.Key, 20),
				truncate(p.Name, 20),
				truncate(p.Domain, 20),
				truncate(p.Type, 10),
				truncate(p.Status, 12),
			)
		}
		return tw.Flush()
	}
}

// FormatProject formats a single project.
func (f *Formatter) FormatProject(project *Project) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(project)
	case FormatCSV, FormatQuiet:
		return nil
	default: // table
		f.Printf("Project: %s\n", project.Key)
		f.Printf("  Name:        %s\n", project.Name)
		f.Printf("  Domain:      %s\n", project.Domain)
		f.Printf("  Type:        %s\n", project.Type)
		f.Printf("  Status:      %s\n", project.Status)
		f.Printf("  Path:        %s\n", project.Path)
		if project.Description != "" {
			f.Printf("  Description: %s\n", project.Description)
		}
		f.Printf("  Created:     %s\n", project.CreatedAt.Format("2006-01-02 15:04:05"))
		f.Printf("  Updated:     %s\n", project.UpdatedAt.Format("2006-01-02 15:04:05"))
		return nil
	}
}

// ==================== Portfolio Formatting ====================

// FormatPortfolioSummary formats the portfolio operating-loop summary.
func (f *Formatter) FormatPortfolioSummary(summary *PortfolioSummary) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(summary)
	case FormatCSV:
		headers := []string{"Metric", "Value"}
		rows := [][]string{
			{"Updated", summary.Updated},
			{"North Star", summary.NorthStar},
			{"Products", fmt.Sprintf("%d", summary.ProductCount)},
			{"Revenue Products", fmt.Sprintf("%d", summary.RevenueProductCount)},
			{"Deployment Ready", fmt.Sprintf("%d", summary.DeploymentReadyCount)},
			{"Analytics Ready", fmt.Sprintf("%d", summary.AnalyticsReadyCount)},
			{"Billing Ready", fmt.Sprintf("%d", summary.BillingReadyCount)},
		}
		stages := make([]string, 0, len(summary.StageCounts))
		for stage := range summary.StageCounts {
			stages = append(stages, stage)
		}
		sort.Strings(stages)
		for _, stage := range stages {
			rows = append(rows, []string{"Stage: " + stage, fmt.Sprintf("%d", summary.StageCounts[stage])})
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		return nil
	default:
		f.Printf("Portfolio Operating Loop\n")
		f.Printf("  Updated:           %s\n", summary.Updated)
		f.Printf("  North Star:        %s\n", summary.NorthStar)
		f.Printf("  Products:          %d\n", summary.ProductCount)
		f.Printf("  Revenue Products:  %d\n", summary.RevenueProductCount)
		f.Printf("  Deploy Ready:      %d\n", summary.DeploymentReadyCount)
		f.Printf("  Analytics Ready:   %d\n", summary.AnalyticsReadyCount)
		f.Printf("  Billing Ready:     %d\n", summary.BillingReadyCount)
		f.Printf("\nStages:\n")
		stages := make([]string, 0, len(summary.StageCounts))
		for stage := range summary.StageCounts {
			stages = append(stages, stage)
		}
		sort.Strings(stages)
		for _, stage := range stages {
			f.Printf("  %-10s %d\n", stage+":", summary.StageCounts[stage])
		}
		if len(summary.PriorityProducts) > 0 {
			f.Printf("\nPriority Products:\n")
			tw := f.NewTableWriter()
			tw.WriteHeader("KEY", "STAGE", "MRR", "NEXT GATE", "NEXT ACTION")
			for _, p := range summary.PriorityProducts {
				tw.WriteRow(
					truncate(p.Key, 24),
					truncate(p.Stage, 10),
					fmt.Sprintf("$%d/$%d", p.CurrentMRR, p.TargetMRR),
					truncate(p.NextGate, 22),
					truncate(p.NextAction, 40),
				)
			}
			return tw.Flush()
		}
		return nil
	}
}

// FormatPortfolioProducts formats portfolio products.
func (f *Formatter) FormatPortfolioProducts(products []PortfolioProduct) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(products)
	case FormatCSV:
		headers := []string{"Key", "Name", "Domain", "Stage", "Status", "Current MRR", "Target MRR", "Next Gate"}
		rows := make([][]string, len(products))
		for i, p := range products {
			rows[i] = []string{
				p.Key,
				p.Name,
				p.Domain,
				p.Stage,
				p.Status,
				fmt.Sprintf("%d", p.CurrentMRR),
				fmt.Sprintf("%d", p.TargetMRR),
				p.NextGate,
			}
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		return nil
	default:
		tw := f.NewTableWriter()
		tw.WriteHeader("KEY", "DOMAIN", "STAGE", "STATUS", "MRR", "NEXT GATE")
		for _, p := range products {
			tw.WriteRow(
				truncate(p.Key, 24),
				truncate(p.Domain, 18),
				truncate(p.Stage, 10),
				truncate(p.Status, 12),
				fmt.Sprintf("$%d/$%d", p.CurrentMRR, p.TargetMRR),
				truncate(p.NextGate, 24),
			)
		}
		return tw.Flush()
	}
}

// FormatPortfolioProduct formats one portfolio product.
func (f *Formatter) FormatPortfolioProduct(product *PortfolioProduct) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(product)
	case FormatCSV, FormatQuiet:
		return nil
	default:
		f.Printf("Product: %s\n", product.Key)
		f.Printf("  Name:             %s\n", product.Name)
		f.Printf("  Domain:           %s\n", product.Domain)
		f.Printf("  Stage:            %s\n", product.Stage)
		f.Printf("  Status:           %s\n", product.Status)
		f.Printf("  Owner:            %s\n", product.Owner)
		f.Printf("  ICP:              %s\n", product.ICP)
		f.Printf("  Repo Path:        %s\n", product.RepoPath)
		f.Printf("  Current MRR:      $%d\n", product.CurrentMRR)
		f.Printf("  Target MRR:       $%d\n", product.TargetMRR)
		f.Printf("  Deploy Ready:     %t\n", product.DeployReady)
		f.Printf("  Analytics Ready:  %t\n", product.AnalyticsReady)
		f.Printf("  Billing Ready:    %t\n", product.BillingReady)
		f.Printf("  Primary Metric:   %s\n", product.PrimaryMetric)
		f.Printf("  Primary Risk:     %s\n", product.PrimaryRisk)
		f.Printf("  Next Gate:        %s\n", product.NextGate)
		f.Printf("  Next Action:      %s\n", product.NextAction)
		return nil
	}
}

// ==================== Context Formatting ====================

// FormatContexts formats contexts according to the output format.
func (f *Formatter) FormatContexts(contexts []Context) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(contexts)
	case FormatCSV:
		headers := []string{"Key", "Domain", "Project", "Type", "Updated"}
		rows := make([][]string, len(contexts))
		for i, c := range contexts {
			rows[i] = []string{
				c.Key,
				c.Domain,
				c.Project,
				c.Type,
				c.UpdatedAt.Format(time.RFC3339),
			}
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		return nil
	default: // table
		tw := f.NewTableWriter()
		tw.WriteHeader("KEY", "DOMAIN", "PROJECT", "TYPE", "UPDATED")
		for _, c := range contexts {
			tw.WriteRow(
				truncate(c.Key, 20),
				truncate(c.Domain, 20),
				truncate(c.Project, 20),
				truncate(c.Type, 12),
				c.UpdatedAt.Format("2006-01-02 15:04"),
			)
		}
		return tw.Flush()
	}
}

// FormatContext formats a single context.
func (f *Formatter) FormatContext(context *Context) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(context)
	case FormatCSV, FormatQuiet:
		return nil
	default: // table
		f.Printf("Context: %s\n", context.Key)
		f.Printf("  ID:          %s\n", context.ID)
		f.Printf("  Domain:      %s\n", context.Domain)
		f.Printf("  Project:     %s\n", context.Project)
		f.Printf("  Type:        %s\n", context.Type)
		f.Printf("  Created:     %s\n", context.CreatedAt.Format("2006-01-02 15:04:05"))
		f.Printf("  Updated:     %s\n", context.UpdatedAt.Format("2006-01-02 15:04:05"))
		if context.ExpiresAt != nil {
			f.Printf("  Expires:     %s\n", context.ExpiresAt.Format("2006-01-02 15:04:05"))
		}
		if len(context.Metadata) > 0 {
			f.Printf("  Metadata:    %v\n", context.Metadata)
		}
		f.Printf("\nContent:\n%s\n", context.Content)
		return nil
	}
}

// ==================== Lane Formatting ====================

// FormatLanes formats lanes according to the output format.
func (f *Formatter) FormatLanes(lanes []Lane) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(lanes)
	case FormatCSV:
		headers := []string{"ID", "Name", "Capabilities", "Max Parallel", "Auto Claim"}
		rows := make([][]string, len(lanes))
		for i, l := range lanes {
			rows[i] = []string{
				l.ID,
				l.Name,
				l.Capabilities,
				fmt.Sprintf("%d", l.MaxParallel),
				fmt.Sprintf("%t", l.AutoClaim),
			}
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		return nil
	default: // table
		tw := f.NewTableWriter()
		tw.WriteHeader("ID", "NAME", "CAPABILITIES", "MAX PARALLEL", "AUTO CLAIM")
		for _, l := range lanes {
			tw.WriteRow(
				truncate(l.ID, 15),
				truncate(l.Name, 20),
				truncate(l.Capabilities, 20),
				fmt.Sprintf("%d", l.MaxParallel),
				fmt.Sprintf("%t", l.AutoClaim),
			)
		}
		return tw.Flush()
	}
}

// FormatLane formats a single lane.
func (f *Formatter) FormatLane(lane *Lane) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(lane)
	case FormatCSV, FormatQuiet:
		return nil
	default: // table
		f.Printf("Lane: %s\n", lane.ID)
		f.Printf("  Name:            %s\n", lane.Name)
		f.Printf("  Capabilities:    %s\n", lane.Capabilities)
		f.Printf("  Max Parallel:    %d\n", lane.MaxParallel)
		f.Printf("  Auto Claim:      %t\n", lane.AutoClaim)
		f.Printf("  Timeout:         %d minutes\n", lane.TimeoutMinutes)
		if lane.Description != "" {
			f.Printf("  Description:     %s\n", lane.Description)
		}
		if lane.CreatedAt != "" {
			f.Printf("  Created:         %s\n", lane.CreatedAt)
		}
		return nil
	}
}

// ==================== Approval Formatting ====================

// FormatApprovals formats approvals according to the output format.
func (f *Formatter) FormatApprovals(approvals []Approval) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(approvals)
	case FormatCSV:
		headers := []string{"ID", "Type", "Tier", "Domain", "Title", "Confidence", "Status", "Expires"}
		rows := make([][]string, len(approvals))
		for i, a := range approvals {
			rows[i] = []string{
				a.ID,
				a.Type,
				a.Tier,
				a.Domain,
				truncate(a.Title, 30),
				fmt.Sprintf("%.2f", a.Confidence),
				a.Status,
				a.ExpiresAt.Format("15:04"),
			}
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		return nil
	default: // table
		tw := f.NewTableWriter()
		tw.WriteHeader("ID", "TYPE", "TIER", "DOMAIN", "TITLE", "CONFIDENCE", "STATUS", "EXPIRES")
		for _, a := range approvals {
			tw.WriteRow(
				truncate(a.ID, 12),
				truncate(a.Type, 10),
				truncate(a.Tier, 8),
				truncate(a.Domain, 15),
				truncate(a.Title, 25),
				fmt.Sprintf("%.2f", a.Confidence),
				truncate(a.Status, 10),
				a.ExpiresAt.Format("15:04"),
			)
		}
		return tw.Flush()
	}
}

// FormatApproval formats a single approval.
func (f *Formatter) FormatApproval(approval *Approval) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(approval)
	case FormatCSV, FormatQuiet:
		return nil
	default: // table
		f.Printf("Approval: %s\n", approval.ID)
		f.Printf("  Type:        %s\n", approval.Type)
		f.Printf("  Tier:        %s\n", approval.Tier)
		f.Printf("  Domain:      %s\n", approval.Domain)
		f.Printf("  Project:     %s\n", approval.Project)
		f.Printf("  Title:       %s\n", approval.Title)
		f.Printf("  Description: %s\n", approval.Description)
		f.Printf("  Confidence:  %.2f\n", approval.Confidence)
		f.Printf("  Risk Score:  %.2f\n", approval.RiskScore)
		f.Printf("  Status:      %s\n", approval.Status)
		f.Printf("  Requested By: %s\n", approval.RequestedBy)
		f.Printf("  Requested:  %s\n", approval.RequestedAt.Format("2006-01-02 15:04"))
		f.Printf("  Expires:    %s (%s)\n", approval.ExpiresAt.Format("15:04"), time.Until(approval.ExpiresAt).Round(time.Minute))
		if approval.Status == "approved" || approval.Status == "rejected" {
			f.Printf("  Decision:    %s\n", approval.Decision)
			f.Printf("  Decided By:  %s\n", approval.DecidedBy)
			if approval.Reason != "" {
				f.Printf("  Reason:      %s\n", approval.Reason)
			}
		}
		return nil
	}
}

// ==================== Queue Formatting ====================

// FormatQueueDepth formats queue depth information.
func (f *Formatter) FormatQueueDepth(depth *QueueDepth) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(depth)
	case FormatQuiet:
		return nil
	default: // table
		f.Printf("Queue Depth:\n")
		f.Printf("  Queued:     %d\n", depth.Queued)
		f.Printf("  Dispatched: %d\n", depth.Dispatched)
		f.Printf("  Running:    %d\n", depth.Running)
		f.Printf("  Blocked:    %d\n", depth.Blocked)
		f.Printf("  Completed:  %d\n", depth.Completed)
		f.Printf("  Failed:     %d\n", depth.Failed)
		f.Printf("  Approved:   %d\n", depth.Approved)
		return nil
	}
}

// FormatQueueItems formats a list of queue items.
func (f *Formatter) FormatQueueItems(items []QueueItem) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(items)
	case FormatQuiet:
		for _, item := range items {
			f.Println(item.ID)
		}
		return nil
	case FormatCSV:
		f.Println("ID,Title,Status,Assignee,Lane,Priority")
		for _, item := range items {
			f.Printf("%s,\"%s\",%s,%s,%s,%s\n", item.ID, item.Title, item.Status, item.Assignee, item.Lane, item.Priority)
		}
		return nil
	default: // table
		f.Println("ID         Title                      Status      Assignee  Lane  Priority")
		f.Println("---------- -------------------------- ----------- --------- ----- --------")
		for _, item := range items {
			f.Printf("%-10s %-26s %-11s %-9s %-5s %s\n",
				item.ID, truncate(item.Title, 26), item.Status, item.Assignee, item.Lane, item.Priority)
		}
		return nil
	}
}

// FormatQueueItemDetail formats a single queue item in detail.
func (f *Formatter) FormatQueueItemDetail(item *QueueItem) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(item)
	case FormatQuiet:
		f.Println(item.ID)
		return nil
	default: // table
		f.Printf("ID:         %s\n", item.ID)
		f.Printf("Title:      %s\n", item.Title)
		f.Printf("Status:     %s\n", item.Status)
		f.Printf("Assignee:   %s\n", item.Assignee)
		f.Printf("Lane:       %s\n", item.Lane)
		f.Printf("Priority:   %s\n", item.Priority)
		f.Printf("Created:    %s\n", item.CreatedAt)
		if item.CompletedAt != "" {
			f.Printf("Completed:  %s\n", item.CompletedAt)
		}
		return nil
	}
}

// truncate truncates a string to maxLen.
func truncate(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen-3] + "..."
}

// ==================== Gate Formatting ====================

// FormatGateStatus formats gate status for a task.
func (f *Formatter) FormatGateStatus(status map[string]interface{}) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(status)
	case FormatCSV, FormatQuiet:
		return nil
	default: // table
		taskID, _ := status["taskId"].(string)
		f.Printf("Gate Status for Task: %s\n", taskID)

		gates, ok := status["gates"].([]interface{})
		if !ok {
			f.Printf("  No gates found\n")
			return nil
		}

		tw := f.NewTableWriter()
		tw.WriteHeader("GATE", "STATUS")
		for _, g := range gates {
			gate, ok := g.(map[string]interface{})
			if !ok {
				continue
			}
			name, _ := gate["name"].(string)
			s, _ := gate["status"].(string)
			tw.WriteRow(name, s)
		}
		return tw.Flush()
	}
}

// ==================== Patrol Formatting ====================

// PatrolInfo represents patrol info for formatting (to avoid import cycle)
type PatrolInfo struct {
	ID        string                 `json:"id"`
	Name      string                 `json:"name"`
	Type      string                 `json:"type"`
	Status    string                 `json:"status"`
	Interval  int                    `json:"interval"`
	LastRun   time.Time              `json:"last_run"`
	NextRun   time.Time              `json:"next_run,omitempty"`
	Message   string                 `json:"message,omitempty"`
	Config    map[string]interface{} `json:"config,omitempty"`
	Successes int                    `json:"successes"`
	Failures  int                    `json:"failures"`
}

// FormatPatrols formats patrols according to the output format.
func (f *Formatter) FormatPatrols(patrols interface{}) error {
	// Convert to []PatrolInfo
	var patrolList []PatrolInfo
	switch p := patrols.(type) {
	case []PatrolInfo:
		patrolList = p
	default:
		return f.WriteJSON(patrols)
	}

	switch f.format {
	case FormatJSON:
		return f.WriteJSON(patrolList)
	case FormatCSV:
		headers := []string{"ID", "Name", "Type", "Status", "Interval", "Last Run"}
		rows := make([][]string, len(patrolList))
		for i, p := range patrolList {
			rows[i] = []string{
				p.ID,
				p.Name,
				p.Type,
				p.Status,
				fmt.Sprintf("%ds", p.Interval),
				formatTimeAgo(p.LastRun),
			}
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		for _, p := range patrolList {
			fmt.Fprintf(f.writer, "%s\n", p.ID)
		}
		return nil
	default: // table
		tw := f.NewTableWriter()
		tw.WriteHeader("ID", "NAME", "TYPE", "STATUS", "LAST RUN", "INTERVAL")
		for _, p := range patrolList {
			tw.WriteRow(
				truncate(p.ID, 15),
				truncate(p.Name, 20),
				truncate(p.Type, 12),
				truncate(p.Status, 10),
				formatTimeAgo(p.LastRun),
				fmt.Sprintf("%ds", p.Interval),
			)
		}
		tw.Flush()
		f.Printf("\nTotal: %d patrols\n", len(patrolList))
		return nil
	}
}

// FormatPatrolStatus formats a single patrol's status.
func (f *Formatter) FormatPatrolStatus(patrol interface{}) error {
	var p *PatrolInfo
	switch pt := patrol.(type) {
	case *PatrolInfo:
		p = pt
	default:
		return f.WriteJSON(patrol)
	}

	switch f.format {
	case FormatJSON:
		return f.WriteJSON(p)
	case FormatCSV, FormatQuiet:
		return nil
	default: // table
		f.Printf("Patrol: %s\n", p.ID)
		f.Printf("  Name:      %s\n", p.Name)
		f.Printf("  Type:      %s\n", p.Type)
		f.Printf("  Status:    %s\n", p.Status)
		f.Printf("  Interval:  %s\n", formatDuration(time.Duration(p.Interval)*time.Second))
		f.Printf("  Last Run:  %s\n", p.LastRun.Format("2006-01-02 15:04:05"))
		if !p.NextRun.IsZero() {
			f.Printf("  Next Run:  %s\n", p.NextRun.Format("2006-01-02 15:04:05"))
		}
		if p.Message != "" {
			f.Printf("  Message:   %s\n", p.Message)
		}
		f.Printf("  Stats:     %d successes, %d failures\n", p.Successes, p.Failures)
		if len(p.Config) > 0 {
			f.Printf("  Config:\n")
			for k, v := range p.Config {
				f.Printf("    %s: %v\n", k, v)
			}
		}
		return nil
	}
}

// formatTimeAgo returns a human-readable "time ago" string.
func formatTimeAgo(t time.Time) string {
	if t.IsZero() {
		return "never"
	}
	d := time.Since(t)
	return formatDuration(d) + " ago"
}

// formatDuration returns a human-readable duration string.
func formatDuration(d time.Duration) string {
	if d < time.Minute {
		return fmt.Sprintf("%ds", int(d.Seconds()))
	}
	if d < time.Hour {
		return fmt.Sprintf("%dm", int(d.Minutes()))
	}
	if d < 24*time.Hour {
		return fmt.Sprintf("%dh", int(d.Hours()))
	}
	return fmt.Sprintf("%dd", int(d.Hours()/24))
}

// ==================== Work Context Formatting (forge work) ====================

// FormatWorkContext formats the current work context for display.
func (f *Formatter) FormatWorkContext(ctx *WorkContext) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(ctx)
	case FormatCSV, FormatQuiet:
		return nil
	default: // table
		setAtStr := ctx.SetAt
		if t, err := time.Parse(time.RFC3339, ctx.SetAt); err == nil {
			setAtStr = t.Format("2006-01-02 15:04:05")
		}
		f.Printf("Domain:  %s\n", ctx.Domain)
		f.Printf("Project: %s\n", ctx.Project)
		f.Printf("Set at:  %s\n", setAtStr)
		if ctx.ShellPrompt != "" {
			f.Printf("Prompt:  %s\n", ctx.ShellPrompt)
		}
		return nil
	}
}

// ==================== Fleet Formatting (V4-011) ====================

// FormatFleetStatus formats fleet status as a table.
func (f *Formatter) FormatFleetStatus(status *FleetStatus) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(status)
	case FormatCSV:
		headers := []string{"Node", "Agents", "Status", "Queue", "CPU%", "RAM%"}
		rows := make([][]string, len(status.Nodes))
		for i, n := range status.Nodes {
			rows[i] = []string{
				n.ID,
				fmt.Sprintf("%d", n.AgentCount),
				n.Status,
				fmt.Sprintf("%d", n.QueueDepth),
				fmt.Sprintf("%d%%", n.CPUPercent),
				fmt.Sprintf("%d%%", n.RAMPercent),
			}
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		for _, n := range status.Nodes {
			f.Println(n.ID)
		}
		return nil
	default: // table
		tw := f.NewTableWriter()
		tw.WriteHeader("NODE", "AGENTS", "STATUS", "QUEUE", "CPU%", "RAM%")
		for _, n := range status.Nodes {
			tw.WriteRow(
				truncate(n.ID, 12),
				fmt.Sprintf("%d", n.AgentCount),
				truncate(n.Status, 10),
				fmt.Sprintf("%d", n.QueueDepth),
				fmt.Sprintf("%d%%", n.CPUPercent),
				fmt.Sprintf("%d%%", n.RAMPercent),
			)
		}
		tw.Flush()
		f.Printf("\nTotal: %d agents across %d nodes\n", status.TotalAgents, status.TotalNodes)
		return nil
	}
}

// FormatFleetHealth formats fleet health report.
func (f *Formatter) FormatFleetHealth(health *FleetHealth) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(health)
	case FormatCSV:
		headers := []string{"Node", "Status", "Agents", "Healthy", "CPU%", "RAM%", "Issues"}
		rows := make([][]string, len(health.Nodes))
		for i, n := range health.Nodes {
			issueStr := "-"
			if len(n.Issues) > 0 {
				issueStr = strings.Join(n.Issues, "; ")
			}
			rows[i] = []string{
				n.NodeID,
				n.Status,
				fmt.Sprintf("%d", n.AgentCount),
				fmt.Sprintf("%t", n.Healthy),
				fmt.Sprintf("%d%%", n.CPUPercent),
				fmt.Sprintf("%d%%", n.RAMPercent),
				issueStr,
			}
		}
		return f.WriteCSV(headers, rows)
	case FormatQuiet:
		return nil
	default: // table
		f.Println("🏥 Fleet Health Report")
		f.Printf("\nTotal Nodes: %d\n", health.TotalNodes)
		if health.TotalNodes > 0 {
			f.Printf("Healthy: %d (%.1f%%)\n", health.Healthy, float64(health.Healthy)*100/float64(health.TotalNodes))
		} else {
			f.Printf("Healthy: 0 (0.0%%)\n")
		}
		f.Printf("Stale: %d\n", health.Stale)
		f.Printf("Overloaded: %d\n", health.Overloaded)

		if len(health.Nodes) > 0 {
			f.Println("\nNode Details:")
			tw := f.NewTableWriter()
			tw.WriteHeader("NODE", "STATUS", "AGENTS", "CPU%", "RAM%", "HEALTHY")
			for _, n := range health.Nodes {
				healthyStr := "✓"
				if !n.Healthy {
					healthyStr = "✗"
				}
				tw.WriteRow(
					truncate(n.NodeID, 12),
					truncate(n.Status, 10),
					fmt.Sprintf("%d", n.AgentCount),
					fmt.Sprintf("%d%%", n.CPUPercent),
					fmt.Sprintf("%d%%", n.RAMPercent),
					healthyStr,
				)
			}
			tw.Flush()
		}

		if len(health.Issues) > 0 {
			f.Println("\n⚠️  Issues:")
			for _, issue := range health.Issues {
				f.Printf("  • %s: %s (%s)\n", issue.NodeID, issue.Type, issue.Details)
			}
		}
		return nil
	}
}

// FormatBroadcastResult formats broadcast operation result.
func (f *Formatter) FormatBroadcastResult(result *BroadcastResult) error {
	switch f.format {
	case FormatJSON:
		return f.WriteJSON(result)
	case FormatCSV, FormatQuiet:
		return nil
	default: // table
		f.Printf("📢 Broadcast [%s] to %d agent(s)\n", strings.ToUpper(result.Type), result.TargetCount)
		f.Printf("   Message: %s\n", result.Message)
		if len(result.Targets) > 0 {
			f.Printf("   Targets: %s\n", strings.Join(result.Targets, ", "))
		}
		if result.Success {
			f.Println("   Status: ✓ Success")
		} else {
			f.Println("   Status: ✗ Failed")
		}
		return nil
	}
}
