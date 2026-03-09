//go:build tmux_bridge
// +build tmux_bridge

package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/gorilla/websocket"
)

// TmuxBridge bridges WebSocket tasks to tmux agent dispatch
type TmuxBridge struct {
	ServerURL string
	AgentID   string
	Conn      *websocket.Conn
	Done      chan struct{}
}

// WSMessage matches v3 protocol
type WSMessage struct {
	Version string          `json:"v"`
	Type    string          `json:"type"`
	ID      string          `json:"id"`
	TaskID  string          `json:"task_id,omitempty"`
	Payload json.RawMessage `json:"payload"`
	Time    time.Time       `json:"ts"`
}

// NewTmuxBridge creates a bridge for an agent
func NewTmuxBridge(agentID, serverURL string) *TmuxBridge {
	return &TmuxBridge{
		AgentID:   agentID,
		ServerURL: serverURL,
		Done:      make(chan struct{}),
	}
}

// Connect registers with v3 server via WebSocket
func (b *TmuxBridge) Connect() error {
	log.Printf("[%s] Connecting bridge to %s", b.AgentID, b.ServerURL)

	conn, _, err := websocket.DefaultDialer.Dial(b.ServerURL, nil)
	if err != nil {
		return fmt.Errorf("dial failed: %w", err)
	}
	b.Conn = conn

	// Register as this agent
	regPayload := map[string]interface{}{
		"agent_id":     b.AgentID,
		"name":         b.AgentID,
		"node":         "sati",
		"tier":         "t2",
		"capabilities": []string{"code", "review", "test"},
	}
	// Convert map to json.RawMessage for proper JSON nesting (not base64)
	payloadBytes, _ := json.Marshal(regPayload)
	var rawPayload json.RawMessage = payloadBytes

	reg := WSMessage{
		Version: "1",
		Type:    "agent.register",
		ID:      generateBridgeID(),
		Payload: rawPayload,
		Time:    time.Now(),
	}

	if err := conn.WriteJSON(reg); err != nil {
		conn.Close()
		return fmt.Errorf("registration failed: %w", err)
	}

	// Wait for ack
	var ack WSMessage
	if err := conn.ReadJSON(&ack); err != nil {
		conn.Close()
		return fmt.Errorf("failed to read ack: %w", err)
	}

	log.Printf("[%s] Bridge connected", b.AgentID)
	return nil
}

// Run handles incoming tasks
func (b *TmuxBridge) Run() {
	defer close(b.Done)

	for {
		var msg WSMessage
		err := b.Conn.ReadJSON(&msg)
		if err != nil {
			log.Printf("[%s] Connection error: %v", b.AgentID, err)
			return
		}

		switch msg.Type {
		case "task.assigned":
			log.Printf("[%s] Received task %s", b.AgentID, msg.TaskID)
			b.handleTask(msg)

		case "ping":
			// Send pong
			pong := WSMessage{
				Version: "1",
				Type:    "pong",
				ID:      generateBridgeID(),
				Time:    time.Now(),
			}
			b.Conn.WriteJSON(pong)
		}
	}
}

// handleTask dispatches to tmux
func (b *TmuxBridge) handleTask(msg WSMessage) {
	// Parse task from payload
	var task struct {
		ID          string `json:"id"`
		Title       string `json:"title"`
		Description string `json:"description"`
		Domain      string `json:"domain"`
		Project     string `json:"project"`
	}

	if err := json.Unmarshal(msg.Payload, &task); err != nil {
		log.Printf("[%s] Failed to parse task: %v", b.AgentID, err)
		return
	}

	// Send task.started
	started := WSMessage{
		Version: "1",
		Type:    "task.started",
		ID:      generateBridgeID(),
		TaskID:  msg.TaskID,
		Time:    time.Now(),
	}
	b.Conn.WriteJSON(started)

	// Dispatch to tmux
	tmuxTarget := fmt.Sprintf("forge:%s", b.AgentID)

	// Create dispatch message
	dispatchMsg := fmt.Sprintf("FORGE_TASK: %s | %s | %s/%s",
		msg.TaskID,
		task.Title,
		task.Domain,
		task.Project,
	)

	log.Printf("[%s] Dispatching to tmux: %s", b.AgentID, dispatchMsg)

	// Send message followed by Enter key
	// Use separate commands for better reliability
	cmd := exec.Command("tmux", "send-keys", "-t", tmuxTarget, dispatchMsg)
	if output, err := cmd.CombinedOutput(); err != nil {
		log.Printf("[%s] Failed to send message to tmux: %v (output: %s)", b.AgentID, err, string(output))
		return
	}

	// Send Enter key separately
	cmd = exec.Command("tmux", "send-keys", "-t", tmuxTarget, "Enter")
	if output, err := cmd.CombinedOutput(); err != nil {
		log.Printf("[%s] Failed to dispatch to tmux: %v (output: %s)", b.AgentID, err, string(output))

		// Send task.failed
		failed := WSMessage{
			Version: "1",
			Type:    "task.failed",
			ID:      generateBridgeID(),
			TaskID:  msg.TaskID,
			Payload: mustJSON(map[string]string{"error": err.Error()}),
			Time:    time.Now(),
		}
		b.Conn.WriteJSON(failed)
		return
	}

	log.Printf("[%s] Task dispatched to tmux", b.AgentID)
	// Monitor the tmux pane for FORGE_COMPLETE for this task.
	go b.monitorTaskCompletion(tmuxTarget, msg.TaskID)
}

// monitorTaskCompletion watches the tmux pane for a FORGE_COMPLETE message
// matching the given task ID and reports completion back to the server.
func (b *TmuxBridge) monitorTaskCompletion(tmuxTarget, taskID string) {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()

	// Safety timeout to avoid leaking goroutines indefinitely.
	timeout := time.After(8 * time.Hour)

	for {
		select {
		case <-timeout:
			log.Printf("[%s] monitorTaskCompletion timeout for task %s", b.AgentID, taskID)
			return
		case <-ticker.C:
			cmd := exec.Command("tmux", "capture-pane", "-p", "-t", tmuxTarget, "-S", "-100")
			output, err := cmd.CombinedOutput()
			if err != nil {
				log.Printf("[%s] Failed to capture tmux pane for %s: %v (output: %s)", b.AgentID, tmuxTarget, err, string(output))
				continue
			}

			scanner := bufio.NewScanner(bytes.NewReader(output))
			for scanner.Scan() {
				line := strings.TrimSpace(scanner.Text())
				if !strings.HasPrefix(line, "FORGE_COMPLETE:") {
					continue
				}

				rest := strings.TrimSpace(strings.TrimPrefix(line, "FORGE_COMPLETE:"))
				if rest == "" {
					continue
				}

				parts := strings.SplitN(rest, "|", 2)
				foundTaskID := strings.TrimSpace(parts[0])
				if foundTaskID != taskID {
					continue
				}

				result := ""
				if len(parts) > 1 {
					result = strings.TrimSpace(parts[1])
				}

				log.Printf("[%s] Detected completion for task %s: %s", b.AgentID, taskID, result)

				payload := mustJSON(map[string]string{
					"result":   result,
					"agent_id": b.AgentID,
				})

				completed := WSMessage{
					Version: "1",
					Type:    "task.completed",
					ID:      generateBridgeID(),
					TaskID:  taskID,
					Payload: payload,
					Time:    time.Now(),
				}

				if err := b.Conn.WriteJSON(completed); err != nil {
					log.Printf("[%s] Failed to send task.completed for %s: %v", b.AgentID, taskID, err)
				}

				return
			}

			if err := scanner.Err(); err != nil {
				log.Printf("[%s] Error scanning tmux output for %s: %v", b.AgentID, tmuxTarget, err)
			}
		}
	}
}

// Close shuts down the bridge
func (b *TmuxBridge) Close() {
	if b.Conn != nil {
		b.Conn.Close()
	}
}

func generateBridgeID() string {
	return fmt.Sprintf("bridge_%d", time.Now().UnixNano())
}

func mustJSON(v interface{}) json.RawMessage {
	b, _ := json.Marshal(v)
	return b
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintf(os.Stderr, "Usage: %s <agent_id> [server_url]\n", os.Args[0])
		os.Exit(1)
	}

	agentID := os.Args[1]
	serverURL := "ws://localhost:8082/ws"
	if len(os.Args) > 2 {
		serverURL = os.Args[2]
	}

	bridge := NewTmuxBridge(agentID, serverURL)

	if err := bridge.Connect(); err != nil {
		log.Fatalf("Failed to connect: %v", err)
	}
	defer bridge.Close()

	log.Printf("[%s] Bridge running. Press Ctrl+C to stop.", agentID)
	bridge.Run()
}
