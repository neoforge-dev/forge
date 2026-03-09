//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"fmt"
	"io"
	"os"
)

// CompletionManager handles shell completion script generation
type CompletionManager struct{}

// NewCompletionManager creates a new completion manager
func NewCompletionManager() *CompletionManager {
	return &CompletionManager{}
}

// GenerateBash generates the bash completion script
func (m *CompletionManager) GenerateBash(w io.Writer) error {
	script := `_forge_completion() {
    local cur prev opts nouns verbs
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    nouns="system git task agent fleet queue docs migrate"

    if [[ ${COMP_CWORD} -eq 1 ]] ; then
        COMPREPLY=( $(compgen -W "${nouns}" -- ${cur}) )
        return 0
    fi

    case "${COMP_WORDS[1]}" in
        system)
            verbs="health metrics patrol config env"
            COMPREPLY=( $(compgen -W "${verbs}" -- ${cur}) )
            ;;
        git)
            verbs="guard status commit push branch merge pull cleanup"
            COMPREPLY=( $(compgen -W "${verbs}" -- ${cur}) )
            ;;
        task)
            verbs="list create show logs cancel retry plan lane"
            COMPREPLY=( $(compgen -W "${verbs}" -- ${cur}) )
            ;;
        agent)
            verbs="list spawn stop status logs"
            COMPREPLY=( $(compgen -W "${verbs}" -- ${cur}) )
            ;;
        fleet)
            verbs="status heartbeat topology"
            COMPREPLY=( $(compgen -W "${verbs}" -- ${cur}) )
            ;;
        queue)
            verbs="depth lanes priority"
            COMPREPLY=( $(compgen -W "${verbs}" -- ${cur}) )
            ;;
        docs)
            verbs="quickstart patterns examples"
            COMPREPLY=( $(compgen -W "${verbs}" -- ${cur}) )
            ;;
        migrate)
            verbs="up down"
            COMPREPLY=( $(compgen -W "${verbs}" -- ${cur}) )
            ;;
    esac

    return 0
}
complete -F _forge_completion forge
`
	_, err := fmt.Fprint(w, script)
	return err
}

// GenerateZsh generates the zsh completion script
func (m *CompletionManager) GenerateZsh(w io.Writer) error {
	script := `#compdef forge

_forge() {
    local line

    _arguments -C \
        "1: :_forge_nouns" \
        "*::arg:->args"

    case $state in
        args)
            case $line[1] in
                system)
                    _values 'verbs' \
                        'health[Check system health]' \
                        'metrics[Show system metrics]' \
                        'patrol[Run maintenance patrols]' \
                        'config[Manage configuration]' \
                        'env[Show environment variables]'
                    ;;
                git)
                    _values 'verbs' \
                        'guard[Manage single-writer git ops]' \
                        'status[Show repository status]' \
                        'commit[Create a git commit]' \
                        'push[Push to remote]' \
                        'branch[Manage branches]' \
                        'merge[Merge branches]' \
                        'pull[Pull from remote]' \
                        'cleanup[Clean stale branches]'
                    ;;
                task)
                    _values 'verbs' \
                        'list[List tasks]' \
                        'create[Create a new task]' \
                        'show[Show task details]' \
                        'logs[Stream task logs]' \
                        'cancel[Cancel a task]' \
                        'retry[Retry a failed task]' \
                        'plan[Manage task plans]' \
                        'lane[Manage task lanes]'
                    ;;
                agent)
                    _values 'verbs' \
                        'list[List agents]' \
                        'spawn[Spawn a new agent]' \
                        'stop[Stop an agent]' \
                        'status[Check agent status]' \
                        'logs[Stream agent logs]'
                    ;;
                fleet)
                    _values 'verbs' \
                        'status[Overall fleet health]' \
                        'heartbeat[Check node heartbeats]' \
                        'topology[Show node graph]'
                    ;;
                queue)
                    _values 'verbs' \
                        'depth[Show queue stats]' \
                        'lanes[List Dark Factory lanes]' \
                        'priority[Adjust task priorities]'
                    ;;
                docs)
                    _values 'verbs' \
                        'quickstart[Interactive tutorial]' \
                        'patterns[Common workflows]' \
                        'examples[Example commands]'
                    ;;
                migrate)
                    _values 'verbs' \
                        'up[Apply migrations]' \
                        'down[Rollback migrations]'
                    ;;
            esac
            ;;
    esac
}

_forge_nouns() {
    local -a nouns
    nouns=(
        'system:Platform operations'
        'git:Repository operations'
        'task:Work units and lifecycle'
        'agent:AI workers in the fleet'
        'fleet:Multi-node orchestration'
        'queue:Task scheduling'
        'docs:Documentation'
        'migrate:Database migrations'
    )
    _describe -t nouns 'forge noun' nouns
}

_forge "$@"
`
	_, err := fmt.Fprint(w, script)
	return err
}

// GenerateFish generates the fish completion script
func (m *CompletionManager) GenerateFish(w io.Writer) error {
	script := `# Completion for forge
complete -c forge -f

# Nouns
complete -c forge -n "__fish_use_subcommand" -a "system" -d "Platform operations"
complete -c forge -n "__fish_use_subcommand" -a "git" -d "Repository operations"
complete -c forge -n "__fish_use_subcommand" -a "task" -d "Work units and lifecycle"
complete -c forge -n "__fish_use_subcommand" -a "agent" -d "AI workers in the fleet"
complete -c forge -n "__fish_use_subcommand" -a "fleet" -d "Multi-node orchestration"
complete -c forge -n "__fish_use_subcommand" -a "queue" -d "Task scheduling"
complete -c forge -n "__fish_use_subcommand" -a "docs" -d "Documentation"
complete -c forge -n "__fish_use_subcommand" -a "migrate" -d "Database migrations"

# Verbs for system
complete -c forge -n "__fish_seen_subcommand_from system" -a "health" -d "Check system health"
complete -c forge -n "__fish_seen_subcommand_from system" -a "metrics" -d "Show system metrics"
complete -c forge -n "__fish_seen_subcommand_from system" -a "patrol" -d "Run maintenance patrols"
complete -c forge -n "__fish_seen_subcommand_from system" -a "config" -d "Manage configuration"
complete -c forge -n "__fish_seen_subcommand_from system" -a "env" -d "Show environment variables"

# Verbs for git
complete -c forge -n "__fish_seen_subcommand_from git" -a "guard status commit push branch merge pull cleanup"

# Verbs for task
complete -c forge -n "__fish_seen_subcommand_from task" -a "list create show logs cancel retry plan lane"

# Verbs for agent
complete -c forge -n "__fish_seen_subcommand_from agent" -a "list spawn stop status logs"

# Verbs for fleet
complete -c forge -n "__fish_seen_subcommand_from fleet" -a "status heartbeat topology"

# Verbs for queue
complete -c forge -n "__fish_seen_subcommand_from queue" -a "depth lanes priority"

# Verbs for docs
complete -c forge -n "__fish_seen_subcommand_from docs" -a "quickstart patterns examples"

# Verbs for migrate
complete -c forge -n "__fish_seen_subcommand_from migrate" -a "up down"
`
	_, err := fmt.Fprint(w, script)
	return err
}

// HandleCompletion handles the completion subcommand
func (m *CompletionManager) HandleCompletion(args []string) {
	if len(args) < 1 {
		fmt.Println("Usage: forge completion [bash|zsh|fish]")
		os.Exit(1)
	}

	shell := args[0]
	var err error

	switch shell {
	case "bash":
		err = m.GenerateBash(os.Stdout)
	case "zsh":
		err = m.GenerateZsh(os.Stdout)
	case "fish":
		err = m.GenerateFish(os.Stdout)
	default:
		fmt.Printf("Unsupported shell: %s\n", shell)
		os.Exit(1)
	}

	if err != nil {
		fmt.Printf("Error generating completion: %v\n", err)
		os.Exit(1)
	}
}
