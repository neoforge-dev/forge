//go:build agent_spawner
// +build agent_spawner

package main

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"strings"
	"time"
)

// AgentConfig defines how to launch each agent type
type AgentConfig struct {
	Name    string
	Command string
	Args    []string
}

// AgentSpawner manages tmux-based agent sessions
var agentConfigs = map[string]AgentConfig{
	"kimi": {
		Name:    "kimi",
		Command: "kimi",
		Args:    []string{"-y"},
	},
	"minimax": {
		Name:    "minimax",
		Command: "minimax",
		Args:    []string{},
	},
	"gemini": {
		Name:    "gemini",
		Command: "gemini",
		Args:    []string{"-y"},
	},
	"pi": {
		Name:    "pi",
		Command: "pi",
		Args:    []string{},
	},
	"opencode": {
		Name:    "opencode",
		Command: "opencode",
		Args:    []string{},
	},
	"kilo": {
		Name:    "kilo",
		Command: "kilo",
		Args:    []string{},
	},
	"cursor": {
		Name:    "cursor",
		Command: "cursor-agent",
		Args:    []string{"-f"},
	},
	"amp": {
		Name:    "amp",
		Command: "amp",
		Args:    []string{"--dangerously-allow-all"},
	},
	"claude": {
		Name:    "claude",
		Command: "claude",
		Args:    []string{"--dangerously-skip-permissions"},
	},
}

// SpawnAgent creates a new tmux window with the specified agent
func SpawnAgent(agentType string) error {
	config, exists := agentConfigs[agentType]
	if !exists {
		return fmt.Errorf("unknown agent type: %s", agentType)
	}

	windowName := config.Name
	tmuxTarget := fmt.Sprintf("forge:%s", windowName)

	// Check if window already exists
	checkCmd := exec.Command("tmux", "has-session", "-t", tmuxTarget)
	if err := checkCmd.Run(); err == nil {
		log.Printf("Agent %s already exists in tmux", windowName)
		return nil
	}

	// Build the command
	args := append(config.Args, "# FORGE v3 Agent - Waiting for tasks...")
	cmdStr := fmt.Sprintf("%s %s", config.Command, strings.Join(args, " "))

	// Create new tmux window
	newWindowCmd := exec.Command("tmux", "new-window", "-t", "forge", "-n", windowName, "-d", cmdStr)
	if output, err := newWindowCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("failed to spawn agent %s: %v (output: %s)", agentType, err, string(output))
	}

	log.Printf("Spawned agent %s in tmux window forge:%s", agentType, windowName)
	time.Sleep(1 * time.Second) // Give agent time to start

	return nil
}

// SpawnAllAgents spawns the standard fleet
func SpawnAllAgents() error {
	log.Println("Spawning FORGE agent fleet...")

	agents := []string{"kimi", "gemini", "minimax", "pi", "opencode"}

	for _, agent := range agents {
		if err := SpawnAgent(agent); err != nil {
			log.Printf("Failed to spawn %s: %v", agent, err)
			// Continue with other agents
		}
	}

	log.Println("Fleet spawn complete")
	return nil
}

// DispatchToAgent sends a task to an agent via tmux
func DispatchToAgent(agentName, taskID, content string) error {
	tmuxTarget := fmt.Sprintf("forge:%s", agentName)

	// Format the message for the agent
	message := fmt.Sprintf("FORGE_TASK: %s | %s", taskID, content)

	// Send to tmux window
	cmd := exec.Command("tmux", "send-keys", "-t", tmuxTarget, message, "Enter")
	if output, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("failed to dispatch to %s: %v (output: %s)", agentName, err, string(output))
	}

	log.Printf("Dispatched task %s to %s", taskID, agentName)
	return nil
}

// ListActiveAgents returns list of active tmux windows in forge session
func ListActiveAgents() ([]string, error) {
	cmd := exec.Command("tmux", "list-windows", "-t", "forge", "-F", "#{window_name}")
	output, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("failed to list tmux windows: %v", err)
	}

	windows := strings.Split(strings.TrimSpace(string(output)), "\n")
	return windows, nil
}

// IsAgentWindowActive checks if an agent window exists
func IsAgentWindowActive(agentName string) bool {
	tmuxTarget := fmt.Sprintf("forge:%s", agentName)
	cmd := exec.Command("tmux", "has-session", "-t", tmuxTarget)
	return cmd.Run() == nil
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("Usage: agent_spawner <command> [args...]")
		fmt.Println("")
		fmt.Println("Commands:")
		fmt.Println("  spawn <agent>     - Spawn specific agent")
		fmt.Println("  spawn-all         - Spawn standard fleet")
		fmt.Println("  dispatch <agent> <task> <content>  - Dispatch task")
		fmt.Println("  list              - List active agents")
		fmt.Println("")
		fmt.Println("Agents: kimi, gemini, minimax, pi, opencode, kilo, cursor, amp, claude")
		os.Exit(1)
	}

	command := os.Args[1]

	switch command {
	case "spawn":
		if len(os.Args) < 3 {
			fmt.Println("Usage: agent_spawner spawn <agent>")
			os.Exit(1)
		}
		if err := SpawnAgent(os.Args[2]); err != nil {
			log.Fatal(err)
		}

	case "spawn-all":
		if err := SpawnAllAgents(); err != nil {
			log.Fatal(err)
		}

	case "dispatch":
		if len(os.Args) < 5 {
			fmt.Println("Usage: agent_spawner dispatch <agent> <task_id> <content>")
			os.Exit(1)
		}
		if err := DispatchToAgent(os.Args[2], os.Args[3], os.Args[4]); err != nil {
			log.Fatal(err)
		}

	case "list":
		agents, err := ListActiveAgents()
		if err != nil {
			log.Fatal(err)
		}
		fmt.Println("Active agents:")
		for _, agent := range agents {
			fmt.Printf("  - %s\n", agent)
		}

	default:
		fmt.Printf("Unknown command: %s\n", command)
		os.Exit(1)
	}
}
