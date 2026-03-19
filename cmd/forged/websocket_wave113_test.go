//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestWave113_SendToAgent_Unknown(t *testing.T) {
	hub := NewHub()
	go hub.Run()
	defer hub.Stop()

	// Target: SendToAgent unknown agent
	msg := WSMessage{Type: MsgPing}
	if ok := hub.SendToAgent("non-existent", msg); ok {
		t.Error("Expected SendToAgent to return false for unknown agent")
	}
}

func TestWave113_SendToWorker_NoWorker(t *testing.T) {
	hub := NewHub()
	go hub.Run()
	defer hub.Stop()

	// Target: SendToWorker no-worker path (line 581 in original, likely moved)
	task := Task{ID: "task-113"}
	// Should not panic, just log
	hub.SendToWorker("unknown-worker", MsgTaskAssigned, "task-113", task)
}

func TestWave113_WritePump_ClosedChannel(t *testing.T) {
	// Skipped: manually closing the internal Send channel races with the hub's
	// writePump goroutine, causing unrecoverable panics in concurrent tests.
	t.Skip("unsafe internal channel manipulation — skipped to avoid test-suite panic")
}

func TestWave113_WritePump_WriteError(t *testing.T) {
	hub := NewHub()
	go hub.Run()
	defer hub.Stop()

	server := httptest.NewServer(WebSocketHandler(hub))
	defer server.Close()

	wsURL := strings.Replace(server.URL, "http", "ws", 1)
	ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatalf("Failed to connect: %v", err)
	}

	agentID := "test-agent-error-113"
	regPayload, _ := json.Marshal(map[string]interface{}{
		"agent_id": agentID,
	})
	ws.WriteJSON(WSMessage{
		Version: WSProtocolVersion,
		Type:    MsgAgentRegister,
		Payload: regPayload,
		Time:    time.Now(),
	})

	var ack WSMessage
	ws.ReadJSON(&ack)

	// Wait for registration
	time.Sleep(100 * time.Millisecond)

	// Close the connection on the client side abruptly
	ws.Close()

	// Small delay to ensure connection is really closed
	time.Sleep(100 * time.Millisecond)

	// Target: writePump WriteJSON error path
	// Sending a message should trigger a write error in writePump
	hub.SendToAgent(agentID, WSMessage{Type: MsgPing})

	// Wait for writePump to encounter error and exit
	time.Sleep(100 * time.Millisecond)
}
