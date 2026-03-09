//go:build worker_client
// +build worker_client

package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
)

// WSMessage matches the server format
type WSMessage struct {
	Version string          `json:"v"`
	Type    string          `json:"type"`
	ID      string          `json:"id"`
	TaskID  string          `json:"task_id,omitempty"`
	Payload json.RawMessage `json:"payload"`
	Time    time.Time       `json:"ts"`
}

// Message types (matching server)
const (
	MsgAgentRegister    = "agent.register"
	MsgAgentRegisterAck = "agent.register_ack"
	MsgTaskAssigned     = "task.assigned"
	MsgTaskStarted      = "task.started"
	MsgTaskCompleted    = "task.completed"
	MsgTaskFailed       = "task.failed"
	MsgPong             = "pong"
	MsgPing             = "ping"
)

// WorkerClient represents a WebSocket-connected worker
type WorkerClient struct {
	ID        string
	Conn      *websocket.Conn
	ServerURL string
	Done      chan struct{}
}

// NewWorkerClient creates a new worker client
func NewWorkerClient(id, serverURL string) *WorkerClient {
	return &WorkerClient{
		ID:        id,
		ServerURL: serverURL,
		Done:      make(chan struct{}),
	}
}

// generateID creates a unique ID
func generateID() string {
	return fmt.Sprintf("msg_%d", time.Now().UnixNano())
}

// Connect establishes WebSocket connection and registers
func (w *WorkerClient) Connect() error {
	log.Printf("[%s] Connecting to %s", w.ID, w.ServerURL)

	conn, _, err := websocket.DefaultDialer.Dial(w.ServerURL, nil)
	if err != nil {
		return fmt.Errorf("dial failed: %w", err)
	}
	w.Conn = conn

	// Prepare registration payload
	regPayload := map[string]interface{}{
		"agent_id":     w.ID,
		"name":         w.ID,
		"node":         "sati",
		"tier":         "standard",
		"capabilities": []string{"code", "review", "test"},
	}
	payloadBytes, _ := json.Marshal(regPayload)

	// Send registration using WSMessage envelope
	reg := WSMessage{
		Version: "1",
		Type:    MsgAgentRegister,
		ID:      generateID(),
		Payload: payloadBytes,
		Time:    time.Now(),
	}

	if err := conn.WriteJSON(reg); err != nil {
		conn.Close()
		return fmt.Errorf("registration failed: %w", err)
	}

	// Wait for acknowledgment
	var ack WSMessage
	if err := conn.ReadJSON(&ack); err != nil {
		conn.Close()
		return fmt.Errorf("failed to read ack: %w", err)
	}

	if ack.Type != MsgAgentRegisterAck {
		conn.Close()
		return fmt.Errorf("expected register_ack, got: %s", ack.Type)
	}

	log.Printf("[%s] Connected and registered", w.ID)
	return nil
}

// Run starts the message handling loop
func (w *WorkerClient) Run() {
	defer close(w.Done)

	// Set up pong handler
	w.Conn.SetPongHandler(func(string) error {
		log.Printf("[%s] Received pong", w.ID)
		return nil
	})

	for {
		var msg WSMessage
		err := w.Conn.ReadJSON(&msg)
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseAbnormalClosure) {
				log.Printf("[%s] Connection error: %v", w.ID, err)
			} else {
				log.Printf("[%s] Read error: %v", w.ID, err)
			}
			return
		}

		// Process message based on type
		switch msg.Type {
		case MsgPing:
			// Send pong
			pong := WSMessage{
				Version: "1",
				Type:    MsgPong,
				ID:      generateID(),
				Time:    time.Now(),
			}
			if err := w.Conn.WriteJSON(pong); err != nil {
				log.Printf("[%s] Failed to send pong: %v", w.ID, err)
				return
			}

		case MsgTaskAssigned:
			log.Printf("[%s] Received task %s", w.ID, msg.TaskID)
			w.handleTask(msg)

		case MsgAgentRegisterAck:
			log.Printf("[%s] Registration acknowledged", w.ID)

		default:
			log.Printf("[%s] Received message type: %s", w.ID, msg.Type)
		}
	}
}

// handleTask processes a received task
func (w *WorkerClient) handleTask(msg WSMessage) {
	log.Printf("[%s] Processing task %s...", w.ID, msg.TaskID)

	// Simulate work
	time.Sleep(100 * time.Millisecond)

	// Send task started
	started := WSMessage{
		Version: "1",
		Type:    MsgTaskStarted,
		ID:      generateID(),
		TaskID:  msg.TaskID,
		Time:    time.Now(),
	}
	w.Conn.WriteJSON(started)

	// Simulate more work
	time.Sleep(500 * time.Millisecond)

	// Send task completed
	resultPayload := map[string]string{
		"result": fmt.Sprintf("Worker %s completed task %s", w.ID, msg.TaskID),
	}
	payloadBytes, _ := json.Marshal(resultPayload)

	completed := WSMessage{
		Version: "1",
		Type:    MsgTaskCompleted,
		ID:      generateID(),
		TaskID:  msg.TaskID,
		Payload: payloadBytes,
		Time:    time.Now(),
	}
	w.Conn.WriteJSON(completed)

	log.Printf("[%s] Task %s completed", w.ID, msg.TaskID)
}

// Close cleanly shuts down the connection
func (w *WorkerClient) Close() {
	if w.Conn != nil {
		w.Conn.Close()
	}
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintf(os.Stderr, "Usage: %s <worker_id> [server_url]\n", os.Args[0])
		fmt.Fprintf(os.Stderr, "Example: %s kimi ws://localhost:8082/ws\n", os.Args[0])
		os.Exit(1)
	}

	workerID := os.Args[1]
	serverURL := "ws://localhost:8082/ws"
	if len(os.Args) > 2 {
		serverURL = os.Args[2]
	}

	client := NewWorkerClient(workerID, serverURL)

	// Handle signals
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	// Connect
	if err := client.Connect(); err != nil {
		log.Fatalf("[%s] Failed to connect: %v", workerID, err)
	}
	defer client.Close()

	// Run in background
	go client.Run()

	log.Printf("[%s] Worker running. Press Ctrl+C to stop.", workerID)

	// Wait for signal
	<-sigChan
	log.Printf("[%s] Shutting down...", workerID)
}
