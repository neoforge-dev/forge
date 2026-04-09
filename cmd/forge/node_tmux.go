package main

import (
	"fmt"
	"os/exec"
	"strings"
)

// ensureTmuxSession creates the tmux session if it does not already exist.
// Returns nil if the session already exists or was created successfully.
func ensureTmuxSession(name string) error {
	if tmuxHasSession(name) {
		return nil
	}
	// new-session -d: detached; -s: session name; -n: first window name
	out, err := exec.Command("tmux", "new-session", "-d", "-s", name, "-n", name).CombinedOutput()
	if err != nil {
		return fmt.Errorf("tmux new-session %q: %w — %s", name, err, strings.TrimSpace(string(out)))
	}
	return nil
}

// ensureTmuxWindow creates a new window in the session if one with that name
// does not already exist. Returns nil if already present or created successfully.
// Uses -a (append) to avoid index conflicts with existing windows.
func ensureTmuxWindow(session, window string) error {
	if tmuxWindowExists(session, window) {
		return nil
	}
	// Use -a flag to append after current windows, avoiding index conflicts
	out, err := exec.Command("tmux", "new-window", "-a", "-t", session, "-n", window).CombinedOutput()
	if err != nil {
		// If the error contains "index ... in use", the window may exist at a different index
		// In this case, we check again and return success if it exists
		errStr := strings.TrimSpace(string(out))
		if strings.Contains(errStr, "index") && strings.Contains(errStr, "in use") {
			// Window likely exists at a different index, check again
			if tmuxWindowExists(session, window) {
				return nil
			}
		}
		return fmt.Errorf("tmux new-window %q in session %q: %w — %s", window, session, err, errStr)
	}
	return nil
}

// sendToTmuxWindow sends a shell command string to a tmux window.
// The command is typed into the window as if the user had pressed Enter.
func sendToTmuxWindow(session, window, command string) error {
	target := session + ":" + window
	// Send the command text (no newline yet)
	if out, err := exec.Command("tmux", "send-keys", "-t", target, command, "").CombinedOutput(); err != nil {
		return fmt.Errorf("tmux send-keys to %s: %w — %s", target, err, strings.TrimSpace(string(out)))
	}
	// Send Enter separately so the command executes
	if out, err := exec.Command("tmux", "send-keys", "-t", target, "Enter").CombinedOutput(); err != nil {
		return fmt.Errorf("tmux send-keys Enter to %s: %w — %s", target, err, strings.TrimSpace(string(out)))
	}
	return nil
}

// killTmuxSession kills the named tmux session. Returns nil if the session
// did not exist (idempotent) or was killed successfully.
func killTmuxSession(name string) error {
	if !tmuxHasSession(name) {
		return nil
	}
	out, err := exec.Command("tmux", "kill-session", "-t", name).CombinedOutput()
	if err != nil {
		return fmt.Errorf("tmux kill-session %q: %w — %s", name, err, strings.TrimSpace(string(out)))
	}
	return nil
}
