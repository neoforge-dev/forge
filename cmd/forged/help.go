//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"
)

// HelpSystem provides interactive tutorials and progressive disclosure help
type HelpSystem struct{}

// NewHelpSystem creates a new help system
func NewHelpSystem() *HelpSystem {
	return &HelpSystem{}
}

// RunHelp handles the help command with progressive disclosure
func RunHelp(args []string) {
	hs := NewHelpSystem()

	if len(args) == 0 {
		// Top-level help (forge --help or forge help)
		hs.ShowRootHelp()
		return
	}

	topic := args[0]

	// Check if this is a noun-verb combination (e.g., "task create")
	if len(args) >= 2 {
		hs.ShowVerbHelp(topic, args[1])
		return
	}

	switch topic {
	case "task":
		hs.ShowTaskHelp()
	case "agent":
		hs.ShowAgentHelp()
	case "fleet":
		hs.ShowFleetHelp()
	case "queue":
		hs.ShowQueueHelp()
	case "git":
		hs.ShowGitHelp()
	case "system":
		hs.ShowSystemHelp()
	case "docs":
		hs.ShowDocsHelp()
	case "quickstart":
		hs.RunQuickstart()
	case "patterns":
		hs.ShowPatterns()
	default:
		fmt.Printf("Unknown help topic: %s\n", topic)
		hs.ShowRootHelp()
	}
}

// ShowRootHelp displays the top-level forge help
func (hs *HelpSystem) ShowRootHelp() {
	fmt.Print(`
FORGE - Fleet Orchestration and Resource Governance Engine

Usage: forge <noun> <verb> [flags]

Nouns (resources you manage):
  task        Work units and their lifecycle
  agent       AI workers in the fleet
  fleet       Multi-node orchestration
  queue       Task scheduling and prioritization
  git         Repository operations with idempotency
  system      Health, config, and maintenance
  docs        Documentation and tutorials

Flags:
  -h, --help       Show help (this screen)
  -v, --version    Show version
      --debug      Enable debug logging

Examples:
  forge task list                    # List all tasks
  forge agent spawn codex           # Spawn a new agent
  forge --help task                 # Learn about tasks

Learn more:
  forge docs quickstart             # Interactive tutorial
  forge docs patterns               # Common workflows
`)
}

// ShowTaskHelp displays help for task commands
func (hs *HelpSystem) ShowTaskHelp() {
	fmt.Print(`
Manage tasks in the FORGE system

Usage: forge task <command> [flags]

Commands:
  create    Create a new task from features.json
  list      List tasks with filtering
  show      Display detailed task information
  logs      Stream task execution logs
  cancel    Cancel a pending or running task
  retry     Retry a failed task
  plan      Create/refresh task plan

Flags:
  -d, --domain string      Filter by domain
  -p, --project string     Filter by project
  -s, --status strings     Filter by status (pending,running,completed,failed)
  -l, --limit int          Limit results (default 50)
      --format string      Output format: table|json|yaml (default "table")

Global Flags:
  -c, --config string      Config file (default ~/.forgerc)

Examples:
  # List recent failed tasks
  forge task list --status failed --limit 10

  # Get task details as JSON
  forge task show TASK-001 --format json

  # Create from features.json
  forge task create --feature-id AUTH-001

Next steps:
  forge help task create        # Learn about creating tasks
  forge task show TASK-001     # View task details
`)
}

// ShowVerbHelp displays help for a specific verb
func (hs *HelpSystem) ShowVerbHelp(noun, verb string) {
	combo := noun + " " + verb

	switch combo {
	case "task create":
		fmt.Print(`
Create a new task from features.json or CLI arguments

Usage: forge task create [flags]

Flags:
  -f, --feature-id string    Feature ID from features.json
      --type string          Task type: feature|bugfix|refactor|research
      --priority int         Priority 0-100 (default 50)
      --agent string         Assign to specific agent
      --lane string          Start in lane: dev|test|deploy
  -n, --dry-run             Show what would be created

Examples:
  # Create from features.json
  forge task create --feature-id AUTH-001

  # Create with explicit parameters
  forge task create --type bugfix --priority 90 \
    --title "Fix login timeout" \
    --description "Users report 30s delays"

  # Preview before creating
  forge task create --feature-id AUTH-001 --dry-run

Related:
  forge task list              # List tasks
  forge task show <id>        # View task details
`)

	case "task list":
		fmt.Print(`
List tasks with filtering and sorting

Usage: forge task list [flags]

Flags:
  -d, --domain string        Filter by domain
  -p, --project string       Filter by project
  -s, --status strings       Filter by status (pending,running,completed,failed)
  -l, --limit int            Limit results (default 50)
      --format string        Output format: table|json|yaml (default "table")
      --since string         Filter by time (e.g., 1h, 24h, 7d)

Examples:
  # List all pending tasks
  forge task list --status pending

  # List failed tasks in last hour
  forge task list --status failed --since 1h

  # List as JSON for scripting
  forge task list --format json | jq '.[]'

Related:
  forge task show <id>      # View task details
  forge task logs <id>       # Stream logs
`)

	case "task show":
		fmt.Print(`
Display detailed task information

Usage: forge task show <task-id> [flags]

Flags:
      --format string        Output format: table|json|yaml (default "table")
  -v, --verbose             Show full details

Examples:
  # View task details
  forge task show TASK-001

  # View as JSON
  forge task show TASK-001 --format json

  # View with full details
  forge task show TASK-001 --verbose

Related:
  forge task logs <id>       # Stream logs
  forge task list            # List tasks
`)

	case "agent spawn":
		fmt.Print(`
Spawn a new agent in the fleet

Usage: forge agent spawn <agent-type> [flags]

Agent Types:
  claude    Claude agent (Code, Opus, Sonnet, Haiku)
  kimi      Kimi agent
  glm       GLM agent
  gemini    Gemini agent
  opencode  OpenCode agent

Flags:
  -n, --node string          Target node (prya, nova, sati, vega)
      --domain string        Initial domain context
  -p, --project string       Initial project context

Examples:
  # Spawn Claude Sonnet on default node
  forge agent spawn claude --model sonnet

  # Spawn on specific node
  forge agent spawn kimi --node nova

Related:
  forge agent list           # List all agents
  forge agent logs <id>      # View agent logs
`)

	default:
		fmt.Printf("No specific help available for '%s %s'\n", noun, verb)
		fmt.Println("\nTry one of these:")
		fmt.Println("  forge help task")
		fmt.Println("  forge help agent")
		fmt.Println("  forge help git")
		fmt.Println("  forge help system")
	}
}

// ShowAgentHelp displays help for agent commands
func (hs *HelpSystem) ShowAgentHelp() {
	fmt.Print(`
Manage AI workers in the FORGE fleet

Usage: forge agent <command> [flags]

Commands:
  list         Show all connected agents
  spawn        Start a new agent
  stop         Gracefully shutdown an agent
  status       Check agent health and status
  logs         Stream agent execution logs

Flags:
  -n, --node string          Filter by node
      --status string        Filter by status (online,idle,busy,offline)

Global Flags:
  -c, --config string        Config file (default ~/.forgerc)

Examples:
  # List all agents
  forge agent list

  # Spawn a new agent
  forge agent spawn claude

  # Check agent status
  forge agent status kimi-001

Next steps:
  forge help agent spawn    # Learn about spawning agents
  forge fleet status        # See overall fleet health
`)
}

// ShowFleetHelp displays help for fleet commands
func (hs *HelpSystem) ShowFleetHelp() {
	fmt.Print(`
Multi-node orchestration and fleet management

Usage: forge fleet <command> [flags]

Commands:
  status       Overall fleet health and topology
  heartbeat    Check node heartbeats
  topology     Show node graph and connections

Flags:
      --format string        Output format: table|json (default "table")

Examples:
  # Check overall fleet status
  forge fleet status

  # View node topology
  forge fleet topology

  # Check heartbeats
  forge fleet heartbeat

Related:
  forge system health       # System-level health
  forge agent list          # List agents
`)
}

// ShowQueueHelp displays help for queue commands
func (hs *HelpSystem) ShowQueueHelp() {
	fmt.Print(`
Task scheduling and prioritization

Usage: forge queue <command> [flags]

Commands:
  depth        Show queue depth and statistics
  lanes        List Dark Factory lanes
  priority     Adjust task priorities

Flags:
  -l, --limit int            Limit results

Examples:
  # View queue depth
  forge queue depth

  # List all lanes
  forge queue lanes

Related:
  forge task list            # List tasks
  forge task create         # Create task
`)
}

// ShowGitHelp displays help for git commands
func (hs *HelpSystem) ShowGitHelp() {
	fmt.Print(`
Repository operations with idempotency

Usage: forge git <command> [flags]

Commands:
  guard        Single-writer git lock (prevents conflicts)
  status       Repository status overview
  commit       Safe commit with idempotency
  cleanup      Clean stale branches

Flags:
      --format string        Output format (default "table")

Examples:
  # Check if git is available for commits
  forge git guard

  # View repository status
  forge git status

  # Run cleanup
  forge git cleanup

Next:
  forge git guard --action commit   # Perform safe commit
  forge system patrol              # Run git cleanup patrol
`)
}

// ShowSystemHelp displays help for system commands
func (hs *HelpSystem) ShowSystemHelp() {
	fmt.Print(`
Health, configuration, and maintenance

Usage: forge system <command> [flags]

Commands:
  health       Check system health status
  metrics      Show system metrics
  patrol       Run maintenance patrols
  config       View/modify configuration

Examples:
  # Check health
  forge system health

  # Run patrols
  forge system patrol

  # View config
  forge system config show

Related:
  forge fleet status          # Fleet-level status
  forge queue depth           # Queue statistics
`)
}

// ShowDocsHelp displays help for docs commands
func (hs *HelpSystem) ShowDocsHelp() {
	fmt.Print(`
Documentation and tutorials

Usage: forge docs <command> [flags]

Commands:
  quickstart    Interactive tutorial for new users
  patterns      Common workflow patterns
  examples      Example commands

Flags:
  -v, --verbose             Show detailed examples

Examples:
  # Start interactive tutorial
  forge docs quickstart

  # View common patterns
  forge docs patterns

  # See examples
  forge docs examples
`)
}

// RunQuickstart runs an interactive tutorial
func (hs *HelpSystem) RunQuickstart() {
	fmt.Print(`
=== FORGE Quickstart Tutorial ===

Welcome to FORGE! This interactive tutorial will help you get started.

Step 1: Understand the Basics
------------------------------
FORGE manages AI agents that work on your projects. The basic workflow is:

1. Create a task (from features or manually)
2. Agents pick up and execute tasks
3. Monitor progress via logs
4. Review and approve results

Let's practice!

Step 2: List Available Tasks
----------------------------
First, let's see what tasks exist:

    forge task list

This shows all tasks with their status (pending, running, completed, failed).

Try it now. Press Enter when ready...
`)

	hs.waitForEnter()

	fmt.Print(`
Step 3: Create a Task
---------------------
To create a task from features.json:

    forge task create --feature-id FEATURE-001

Or with explicit parameters:

    forge task create --type bugfix --priority 90 \
      --title "Fix the login bug"

Step 4: Monitor Task Progress
----------------------------
To watch a task execute:

    forge task logs TASK-001 --follow

Or view details:

    forge task show TASK-001

Step 5: Check Fleet Status
--------------------------
To see all agents:

    forge agent list

To see overall fleet health:

    forge fleet status

=== Tutorial Complete! ===

You've learned the basics. Here's what to try next:

1. Explore more commands:
   forge help task
   forge help agent

2. Run common patterns:
   forge docs patterns

3. Check system health:
   forge system health

Need more help? Use 'forge --help' or 'forge help <topic>'
`)

	hs.waitForEnter()
}

// ShowPatterns displays common workflow patterns
func (hs *HelpSystem) ShowPatterns() {
	fmt.Print(`
=== FORGE Common Patterns ===

1. Hotfix Workflow
------------------
Quickly patch and deploy critical fixes:

    # Create high-priority bugfix task
    forge task create --type bugfix --priority 100 \
      --title "Fix critical bug"

    # Monitor execution
    forge task logs <task-id> --follow

    # Verify fix
    forge git status

    # Deploy
    forge lane promote <task-id> --to deploy

2. Feature Development
----------------------
Standard feature implementation:

    # Create from features.json
    forge task create --feature-id AUTH-001

    # Track progress
    forge task list --status running

    # Review when complete
    forge task show <task-id>

3. Multi-Agent Review
---------------------
Parallel code review:

    # Spawn review agents
    forge agent spawn claude --project backend
    forge agent spawn claude --project frontend

    # Monitor both
    forge agent list

4. Emergency Stop
-----------------
Stop all running tasks:

    # Find running tasks
    forge task list --status running

    # Cancel individually
    forge task cancel <task-id>

    # Or stop specific agents
    forge agent stop <agent-id>

5. Investigation
-----------------
Debug issues:

    # Check fleet health
    forge fleet status

    # View system logs
    forge system health

    # Check patrol results
    forge system patrol

For more details on any command, use 'forge help <command>'
`)
}

func (hs *HelpSystem) waitForEnter() {
	fmt.Print("\nPress Enter to continue...")
	reader := bufio.NewReader(os.Stdin)
	reader.ReadString('\n')
}

// DidYouMean suggests similar commands for typos
func DidYouMean(input string) string {
	commands := map[string][]string{
		"task":  {"task list", "task create", "task show", "task logs"},
		"agent": {"agent list", "agent spawn", "agent status"},
		"git":   {"git guard", "git status", "git commit"},
		"fleet": {"fleet status", "fleet heartbeat"},
		"sys":   {"system health", "system patrol"},
		"patrl": {"system patrol"},
		"guad":  {"git guard"},
		"help":  {"help"},
	}

	input = strings.ToLower(input)
	bestKey := ""
	bestDist := 3 // threshold: only match if distance <= 2
	for key := range commands {
		if strings.Contains(input, key) {
			return strings.Join(commands[key], ", ")
		}
		d := levenshteinDistance(input, key)
		if d < bestDist {
			bestDist = d
			bestKey = key
		}
	}
	if bestKey != "" {
		return strings.Join(commands[bestKey], ", ")
	}
	return ""
}

// levenshteinDistance calculates edit distance between two strings
func levenshteinDistance(a, b string) int {
	if len(a) == 0 {
		return len(b)
	}
	if len(b) == 0 {
		return len(a)
	}

	matrix := make([][]int, len(a)+1)
	for i := range matrix {
		matrix[i] = make([]int, len(b)+1)
		matrix[i][0] = i
	}
	for j := range matrix[0] {
		matrix[0][j] = j
	}

	for i := 1; i <= len(a); i++ {
		for j := 1; j <= len(b); j++ {
			cost := 1
			if a[i-1] == b[j-1] {
				cost = 0
			}
			matrix[i][j] = min(
				matrix[i-1][j]+1,      // deletion
				matrix[i][j-1]+1,      // insertion
				matrix[i-1][j-1]+cost, // substitution
			)
		}
	}

	return matrix[len(a)][len(b)]
}
