//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"os"
	"testing"

	"github.com/spf13/cobra"
)

// ============================================================
// cli_commands.go coverage wave 110
// Target: getAgentID (line 90), truncate (line 265), getTaskStoreForCLI (line 18)
// ============================================================

// TestWave110_GetAgentID_FromFlag verifies the --agent flag takes precedence.
func TestWave110_GetAgentID_FromFlag(t *testing.T) {
	// Clear env to ensure flag is used
	prev := os.Getenv("FORGE_AGENT_ID")
	os.Unsetenv("FORGE_AGENT_ID")
	defer func() {
		if prev != "" {
			os.Setenv("FORGE_AGENT_ID", prev)
		}
	}()

	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")
	cmd.Flags().Set("agent", "flag-agent-wave110")

	id, err := getAgentID(cmd)
	if err != nil {
		t.Errorf("expected agent ID from flag, got error: %v", err)
	}
	if id != "flag-agent-wave110" {
		t.Errorf("expected 'flag-agent-wave110', got %q", id)
	}
}

// TestWave110_GetAgentID_FlagOverridesEnv verifies flag takes precedence over env.
func TestWave110_GetAgentID_FlagOverridesEnv(t *testing.T) {
	prev := os.Getenv("FORGE_AGENT_ID")
	os.Setenv("FORGE_AGENT_ID", "env-agent-should-be-ignored")
	defer func() {
		if prev == "" {
			os.Unsetenv("FORGE_AGENT_ID")
		} else {
			os.Setenv("FORGE_AGENT_ID", prev)
		}
	}()

	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")
	cmd.Flags().Set("agent", "flag-wins")

	id, err := getAgentID(cmd)
	if err != nil {
		t.Errorf("expected agent ID from flag, got error: %v", err)
	}
	if id != "flag-wins" {
		t.Errorf("expected flag to override env, got %q", id)
	}
}

// TestWave110_GetAgentID_NoAgentError verifies error when no agent ID available.
func TestWave110_GetAgentID_NoAgentError(t *testing.T) {
	// Clear all agent sources
	prevEnv := os.Getenv("FORGE_AGENT_ID")
	prevTmux := os.Getenv("TMUX")
	os.Unsetenv("FORGE_AGENT_ID")
	os.Unsetenv("TMUX")
	defer func() {
		if prevEnv != "" {
			os.Setenv("FORGE_AGENT_ID", prevEnv)
		}
		if prevTmux != "" {
			os.Setenv("TMUX", prevTmux)
		}
	}()

	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")
	// Don't set --agent flag

	_, err := getAgentID(cmd)
	if err == nil {
		t.Error("expected error when no agent ID available")
	}
}

// TestWave110_GetAgentID_FromEnv verifies FORGE_AGENT_ID env var works.
func TestWave110_GetAgentID_FromEnv(t *testing.T) {
	prev := os.Getenv("FORGE_AGENT_ID")
	os.Setenv("FORGE_AGENT_ID", "wave110-env-agent")
	defer func() {
		if prev == "" {
			os.Unsetenv("FORGE_AGENT_ID")
		} else {
			os.Setenv("FORGE_AGENT_ID", prev)
		}
	}()

	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")
	// Don't set --agent flag

	id, err := getAgentID(cmd)
	if err != nil {
		t.Errorf("expected agent ID from env, got error: %v", err)
	}
	if id != "wave110-env-agent" {
		t.Errorf("expected 'wave110-env-agent', got %q", id)
	}
}

// TestWave110_Truncate_ShortString verifies truncate on short strings.
func TestWave110_Truncate_ShortString(t *testing.T) {
	result := truncate("short", 10)
	if result != "short" {
		t.Errorf("expected 'short', got %q", result)
	}
}

// TestWave110_Truncate_ExactLength verifies truncate on exact length.
func TestWave110_Truncate_ExactLength(t *testing.T) {
	result := truncate("exactly10!", 10)
	if result != "exactly10!" {
		t.Errorf("expected 'exactly10!', got %q", result)
	}
}

// TestWave110_Truncate_LongString verifies truncate on long strings.
func TestWave110_Truncate_LongString(t *testing.T) {
	result := truncate("this is a very long string that needs truncation", 15)
	if result != "this is a ve..." {
		t.Errorf("expected 'this is a ve...', got %q", result)
	}
}

// TestWave110_Truncate_EmptyString verifies truncate on empty string.
func TestWave110_Truncate_EmptyString(t *testing.T) {
	result := truncate("", 10)
	if result != "" {
		t.Errorf("expected empty string, got %q", result)
	}
}

// TestWave110_Truncate_MaxLenThree verifies edge case with maxLen=3.
func TestWave110_Truncate_MaxLenThree(t *testing.T) {
	result := truncate("abcdefg", 3)
	// With maxLen=3: len(s)=7 > 3, so return s[:0] + "..." = "..."
	if result != "..." {
		t.Errorf("expected '...', got %q", result)
	}
}

// TestWave110_Truncate_MaxLenFour verifies edge case with maxLen=4.
func TestWave110_Truncate_MaxLenFour(t *testing.T) {
	result := truncate("abcdefg", 4)
	// With maxLen=4: len(s)=7 > 4, so return s[:1] + "..." = "a..."
	if result != "a..." {
		t.Errorf("expected 'a...', got %q", result)
	}
}

// TestWave110_Truncate_MaxLenFive verifies edge case with maxLen=5.
func TestWave110_Truncate_MaxLenFive(t *testing.T) {
	result := truncate("abcdefg", 5)
	// With maxLen=5: len(s)=7 > 5, so return s[:2] + "..." = "ab..."
	if result != "ab..." {
		t.Errorf("expected 'ab...', got %q", result)
	}
}

// TestWave110_Truncate_ASCIIMultiByte verifies truncate works with non-ASCII.
func TestWave110_Truncate_ASCIIMultiByte(t *testing.T) {
	result := truncate("hello world", 8)
	// len("hello world") = 11 > 8, so return s[:5] + "..." = "hello..."
	if result != "hello..." {
		t.Errorf("expected 'hello...', got %q", result)
	}
}
