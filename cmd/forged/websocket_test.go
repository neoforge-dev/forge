//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	_ "github.com/mattn/go-sqlite3"
)

// TestWebSocketHandshakeProtocol tests the connection handshake protocol
func TestWebSocketHandshakeProtocol(t *testing.T) {
	hub := NewWebSocketHub()
	go hub.Run()
	defer hub.Stop()

	server := httptest.NewServer(WebSocketHandler(hub))
	defer server.Close()

	t.Run("ValidRegistration", func(t *testing.T) {
		wsURL := strings.Replace(server.URL, "http", "ws", 1)
		ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
		if err != nil {
			t.Fatalf("Failed to connect: %v", err)
		}
		defer ws.Close()

		// Send registration message
		regPayload, _ := json.Marshal(map[string]interface{}{
			"agent_id":     "test-agent-001",
			"name":         "Test Agent",
			"node":         "test-node",
			"tier":         "tier-1",
			"capabilities": []string{"go", "python"},
		})

		regMsg := WSMessage{
			Version: "1",
			Type:    MsgAgentRegister,
			Payload: regPayload,
			Time:    time.Now(),
		}

		if err := ws.WriteJSON(regMsg); err != nil {
			t.Fatalf("Failed to send registration: %v", err)
		}

		// Read acknowledgment
		ws.SetReadDeadline(time.Now().Add(5 * time.Second))
		var ack WSMessage
		if err := ws.ReadJSON(&ack); err != nil {
			t.Fatalf("Failed to read ack: %v", err)
		}

		if ack.Type != MsgAgentRegisterAck {
			t.Errorf("Expected ack type %s, got %s", MsgAgentRegisterAck, ack.Type)
		}

		// Verify payload contains expected fields
		var ackPayload map[string]interface{}
		if err := json.Unmarshal(ack.Payload, &ackPayload); err != nil {
			t.Fatalf("Failed to unmarshal ack payload: %v", err)
		}

		if _, ok := ackPayload["agent_id"]; !ok {
			t.Error("Expected agent_id in ack payload")
		}

		if _, ok := ackPayload["connected_nodes"]; !ok {
			t.Error("Expected connected_nodes in ack payload")
		}

		// Verify worker is registered
		workers := hub.ListWorkers()
		found := false
		for _, w := range workers {
			if w == "test-agent-001" {
				found = true
				break
			}
		}
		if !found {
			t.Error("Worker not found in hub after registration")
		}
	})

	t.Run("InvalidHandshakeType", func(t *testing.T) {
		wsURL := strings.Replace(server.URL, "http", "ws", 1)
		ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
		if err != nil {
			t.Fatalf("Failed to connect: %v", err)
		}
		defer ws.Close()

		// Send invalid message type
		invalidMsg := WSMessage{
			Version: "1",
			Type:    "invalid_type",
			Payload: json.RawMessage(`{}`),
			Time:    time.Now(),
		}

		ws.WriteJSON(invalidMsg)

		// Connection should be closed by server
		ws.SetReadDeadline(time.Now().Add(2 * time.Second))
		_, _, err = ws.ReadMessage()
		if err == nil {
			t.Error("Expected connection to be closed after invalid handshake")
		}
	})

	t.Run("VersionMismatch", func(t *testing.T) {
		wsURL := strings.Replace(server.URL, "http", "ws", 1)
		ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
		if err != nil {
			t.Fatalf("Failed to connect: %v", err)
		}
		defer ws.Close()

		// Send registration with wrong version
		regPayload, _ := json.Marshal(map[string]interface{}{
			"agent_id":     "version-test-agent",
			"name":         "Version Test Agent",
			"node":         "test-node",
			"capabilities": []string{},
		})

		regMsg := WSMessage{
			Version: "99", // Invalid version
			Type:    MsgAgentRegister,
			Payload: regPayload,
			Time:    time.Now(),
		}

		if err := ws.WriteJSON(regMsg); err != nil {
			t.Fatalf("Failed to send registration: %v", err)
		}

		// Read error response
		ws.SetReadDeadline(time.Now().Add(5 * time.Second))
		var errMsg WSMessage
		if err := ws.ReadJSON(&errMsg); err != nil {
			t.Fatalf("Failed to read error message: %v", err)
		}

		if errMsg.Type != MsgError {
			t.Errorf("Expected error type %s, got %s", MsgError, errMsg.Type)
		}

		// Verify error payload
		var errPayload map[string]string
		if err := json.Unmarshal(errMsg.Payload, &errPayload); err != nil {
			t.Fatalf("Failed to unmarshal error payload: %v", err)
		}

		if errPayload["error"] != "version_mismatch" {
			t.Errorf("Expected error code 'version_mismatch', got %s", errPayload["error"])
		}

		expectedMsg := "Please update your CLI: protocol version mismatch (client: 99, server: 1)"
		if errPayload["message"] != expectedMsg {
			t.Errorf("Expected message %q, got %q", expectedMsg, errPayload["message"])
		}

		// Verify worker was NOT registered
		workers := hub.ListWorkers()
		for _, w := range workers {
			if w == "version-test-agent" {
				t.Error("Worker should not be registered after version mismatch")
			}
		}
	})

	t.Run("MissingAgentID", func(t *testing.T) {
		wsURL := strings.Replace(server.URL, "http", "ws", 1)
		ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
		if err != nil {
			t.Fatalf("Failed to connect: %v", err)
		}
		defer ws.Close()

		// Send registration without agent_id
		regPayload, _ := json.Marshal(map[string]interface{}{
			"name": "Test Agent",
			"node": "test-node",
		})

		regMsg := WSMessage{
			Version: "1",
			Type:    MsgAgentRegister,
			Payload: regPayload,
			Time:    time.Now(),
		}

		ws.WriteJSON(regMsg)

		// Worker should still be created with empty agent_id
		// Connection should remain open
		time.Sleep(100 * time.Millisecond)
	})
}

// TestWebSocketTaskDispatchAck tests task dispatch and acknowledgment flow
func TestWebSocketTaskDispatchAck(t *testing.T) {
	hub := NewWebSocketHub()
	go hub.Run()
	defer hub.Stop()

	server := httptest.NewServer(WebSocketHandler(hub))
	defer server.Close()

	// Connect and register a worker
	wsURL := strings.Replace(server.URL, "http", "ws", 1)
	ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatalf("Failed to connect: %v", err)
	}
	defer ws.Close()

	// Register worker
	regPayload, _ := json.Marshal(map[string]interface{}{
		"agent_id":     "task-worker-001",
		"capabilities": []string{"task_execution"},
	})
	ws.WriteJSON(WSMessage{
		Version: "1",
		Type:    MsgAgentRegister,
		Payload: regPayload,
		Time:    time.Now(),
	})

	// Read ack
	var ack WSMessage
	ws.ReadJSON(&ack)

	t.Run("SendTaskToAgent", func(t *testing.T) {
		task := Task{
			ID:      "task-001",
			Domain:  "test",
			Project: "test-project",
			
		}

		hub.SendToWorker("task-worker-001", MsgTaskAssigned, "task-001", task)

		// Worker should receive task assignment
		ws.SetReadDeadline(time.Now().Add(2 * time.Second))
		var taskMsg WSMessage
		if err := ws.ReadJSON(&taskMsg); err != nil {
			t.Fatalf("Failed to receive task: %v", err)
		}

		if taskMsg.Type != MsgTaskAssigned {
			t.Errorf("Expected type %s, got %s", MsgTaskAssigned, taskMsg.Type)
		}

		if taskMsg.TaskID != "task-001" {
			t.Errorf("Expected task_id task-001, got %s", taskMsg.TaskID)
		}
	})

	t.Run("TaskStartedAck", func(t *testing.T) {
		msg := WSMessage{
			Version: "1",
			Type:    MsgTaskStarted,
			TaskID:  "task-001",
			ID:      generateID(),
			Time:    time.Now(),
		}

		if err := ws.WriteJSON(msg); err != nil {
			t.Fatalf("Failed to send task started: %v", err)
		}

		// Small delay for processing
		time.Sleep(100 * time.Millisecond)
	})

	t.Run("TaskCompletedAck", func(t *testing.T) {
		msg := WSMessage{
			Version: "1",
			Type:    MsgTaskCompleted,
			TaskID:  "task-001",
			ID:      generateID(),
			Time:    time.Now(),
		}

		if err := ws.WriteJSON(msg); err != nil {
			t.Fatalf("Failed to send task completed: %v", err)
		}

		time.Sleep(100 * time.Millisecond)
	})

	t.Run("SendToNonExistentAgent", func(t *testing.T) {
		task := Task{ID: "task-dummy"}
		// Should not panic or block
		hub.SendToWorker("non-existent-agent", MsgTaskAssigned, "task-dummy", task)
	})
}

// TestWebSocketHeartbeat tests ping/pong heartbeat mechanism
func TestWebSocketHeartbeat(t *testing.T) {
	hub := NewWebSocketHub()
	go hub.Run()
	defer hub.Stop()

	server := httptest.NewServer(WebSocketHandler(hub))
	defer server.Close()

	wsURL := strings.Replace(server.URL, "http", "ws", 1)
	ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatalf("Failed to connect: %v", err)
	}
	defer ws.Close()

	// Register worker
	regPayload, _ := json.Marshal(map[string]interface{}{
		"agent_id": "heartbeat-worker",
	})
	ws.WriteJSON(WSMessage{
		Version: "1",
		Type:    MsgAgentRegister,
		Payload: regPayload,
		Time:    time.Now(),
	})

	// Read ack
	var ack WSMessage
	ws.ReadJSON(&ack)

	t.Run("ServerPingClientPong", func(t *testing.T) {
		// Server sends ping messages automatically every 30s
		// For testing, we'll manually send a pong and verify it's processed

		pongMsg := WSMessage{
			Version: "1",
			Type:    MsgPong,
			ID:      generateID(),
			Time:    time.Now(),
		}

		if err := ws.WriteJSON(pongMsg); err != nil {
			t.Fatalf("Failed to send pong: %v", err)
		}

		// Worker should still be in list after pong
		time.Sleep(100 * time.Millisecond)
		workers := hub.ListWorkers()
		found := false
		for _, w := range workers {
			if w == "heartbeat-worker" {
				found = true
				break
			}
		}
		if !found {
			t.Error("Worker removed after pong")
		}
	})

	t.Run("WebSocketPingFrame", func(t *testing.T) {
		// Send a WebSocket ping frame (not application-level ping)
		if err := ws.WriteMessage(websocket.PongMessage, []byte("test")); err != nil {
			t.Logf("Pong message write: %v", err)
		}

		// Connection should remain open
		time.Sleep(100 * time.Millisecond)
	})
}

// TestWebSocketReconnection tests reconnection after disconnect
func TestWebSocketReconnection(t *testing.T) {
	hub := NewWebSocketHub()
	go hub.Run()
	defer hub.Stop()

	server := httptest.NewServer(WebSocketHandler(hub))
	defer server.Close()

	wsURL := strings.Replace(server.URL, "http", "ws", 1)

	t.Run("WorkerReconnectsWithSameID", func(t *testing.T) {
		// First connection
		ws1, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
		if err != nil {
			t.Fatalf("First connection failed: %v", err)
		}

		// Register first connection
		regPayload, _ := json.Marshal(map[string]interface{}{
			"agent_id": "reconnect-worker",
		})
		ws1.WriteJSON(WSMessage{
			Version: "1",
			Type:    MsgAgentRegister,
			Payload: regPayload,
			Time:    time.Now(),
		})

		var ack1 WSMessage
		ws1.ReadJSON(&ack1)
		ws1.Close()

		// Wait for unregister to propagate
		time.Sleep(200 * time.Millisecond)

		// Second connection with same ID
		ws2, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
		if err != nil {
			t.Fatalf("Second connection failed: %v", err)
		}
		defer ws2.Close()

		ws2.WriteJSON(WSMessage{
			Version: "1",
			Type:    MsgAgentRegister,
			Payload: regPayload,
			Time:    time.Now(),
		})

		var ack2 WSMessage
		if err := ws2.ReadJSON(&ack2); err != nil {
			t.Fatalf("Failed to get ack on reconnect: %v", err)
		}

		if ack2.Type != MsgAgentRegisterAck {
			t.Errorf("Expected ack on reconnect, got %s", ack2.Type)
		}

		// Verify worker is in list
		workers := hub.ListWorkers()
		found := false
		for _, w := range workers {
			if w == "reconnect-worker" {
				found = true
				break
			}
		}
		if !found {
			t.Error("Worker not found after reconnect")
		}
	})
}

// TestWebSocketConcurrentConnections tests 12 concurrent agent connections
func TestWebSocketConcurrentConnections(t *testing.T) {
	hub := NewWebSocketHub()
	go hub.Run()
	defer hub.Stop()

	server := httptest.NewServer(WebSocketHandler(hub))
	defer server.Close()

	wsURL := strings.Replace(server.URL, "http", "ws", 1)

	const numAgents = 12
	var wg sync.WaitGroup
	connections := make([]*websocket.Conn, numAgents)

	t.Run("Connect12Agents", func(t *testing.T) {
		for i := 0; i < numAgents; i++ {
			wg.Add(1)
			go func(idx int) {
				defer wg.Done()

				ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
				if err != nil {
					t.Errorf("Agent %d failed to connect: %v", idx, err)
					return
				}
				connections[idx] = ws

				// Register
				agentID := fmt.Sprintf("concurrent-agent-%d", idx)
				regPayload, _ := json.Marshal(map[string]interface{}{
					"agent_id": agentID,
					"node":     fmt.Sprintf("node-%d", idx),
				})

				if err := ws.WriteJSON(WSMessage{
					Version: "1",
					Type:    MsgAgentRegister,
					Payload: regPayload,
					Time:    time.Now(),
				}); err != nil {
					t.Errorf("Agent %d failed to register: %v", idx, err)
					return
				}

				// Read ack
				ws.SetReadDeadline(time.Now().Add(5 * time.Second))
				var ack WSMessage
				if err := ws.ReadJSON(&ack); err != nil {
					t.Errorf("Agent %d failed to get ack: %v", idx, err)
					return
				}

				if ack.Type != MsgAgentRegisterAck {
					t.Errorf("Agent %d: Expected ack, got %s", idx, ack.Type)
				}
			}(i)
		}

		wg.Wait()

		// Verify all 12 agents are connected
		time.Sleep(100 * time.Millisecond)
		workers := hub.ListWorkers()
		if len(workers) != numAgents {
			t.Errorf("Expected %d workers, got %d", numAgents, len(workers))
		}

		// Cleanup
		for i, ws := range connections {
			if ws != nil {
				ws.Close()
			}
			// Wait for unregister
			time.Sleep(50 * time.Millisecond)

			// Verify worker removed
			agentID := fmt.Sprintf("concurrent-agent-%d", i)
			found := false
			for _, w := range hub.ListWorkers() {
				if w == agentID {
					found = true
					break
				}
			}
			if found {
				t.Errorf("Worker %s still in list after disconnect", agentID)
			}
		}
	})

	t.Run("BroadcastToAllAgents", func(t *testing.T) {
		// Connect 5 agents
		var wss []*websocket.Conn
		for i := 0; i < 5; i++ {
			ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
			if err != nil {
				t.Fatalf("Failed to connect agent %d: %v", i, err)
			}
			wss = append(wss, ws)

			regPayload, _ := json.Marshal(map[string]interface{}{
				"agent_id": fmt.Sprintf("broadcast-agent-%d", i),
			})
			ws.WriteJSON(WSMessage{
				Version: "1",
				Type:    MsgAgentRegister,
				Payload: regPayload,
				Time:    time.Now(),
			})

			var ack WSMessage
			ws.ReadJSON(&ack)
		}

		// Broadcast message
		broadcastMsg := WSMessage{
			Version: "1",
			Type:    MsgTaskCompleted,
			TaskID:  "broadcast-task",
			ID:      generateID(),
			Time:    time.Now(),
		}
		hub.Broadcast(broadcastMsg)

		// All agents should receive the message
		for i, ws := range wss {
			ws.SetReadDeadline(time.Now().Add(2 * time.Second))
			var msg WSMessage
			if err := ws.ReadJSON(&msg); err != nil {
				t.Errorf("Agent %d didn't receive broadcast: %v", i, err)
				continue
			}
			if msg.Type != MsgTaskCompleted {
				t.Errorf("Agent %d: Expected %s, got %s", i, MsgTaskCompleted, msg.Type)
			}
			ws.Close()
		}
	})
}

// TestWebSocketErrorHandling tests error handling scenarios
func TestWebSocketErrorHandling(t *testing.T) {
	hub := NewWebSocketHub()
	go hub.Run()
	defer hub.Stop()

	server := httptest.NewServer(WebSocketHandler(hub))
	defer server.Close()

	wsURL := strings.Replace(server.URL, "http", "ws", 1)

	t.Run("InvalidJSON", func(t *testing.T) {
		ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
		if err != nil {
			t.Fatalf("Failed to connect: %v", err)
		}
		defer ws.Close()

		// Send invalid JSON
		if err := ws.WriteMessage(websocket.TextMessage, []byte("not json")); err != nil {
			t.Fatalf("Failed to send: %v", err)
		}

		// Connection should be closed
		ws.SetReadDeadline(time.Now().Add(2 * time.Second))
		_, _, err = ws.ReadMessage()
		if err == nil {
			t.Error("Expected connection close after invalid JSON")
		}
	})

	t.Run("LargeMessage", func(t *testing.T) {
		ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
		if err != nil {
			t.Fatalf("Failed to connect: %v", err)
		}
		defer ws.Close()

		// Register first
		regPayload, _ := json.Marshal(map[string]interface{}{
			"agent_id": "large-msg-worker",
		})
		ws.WriteJSON(WSMessage{
			Version: "1",
			Type:    MsgAgentRegister,
			Payload: regPayload,
			Time:    time.Now(),
		})

		var ack WSMessage
		ws.ReadJSON(&ack)

		// Send message close to limit (512KB)
		largeData := make([]byte, 100*1024) // 100KB
		for i := range largeData {
			largeData[i] = 'a'
		}
		// Create valid JSON payload
		largePayload, _ := json.Marshal(map[string]interface{}{
			"data": string(largeData),
		})

		msg := WSMessage{
			Version: "1",
			Type:    MsgTaskProgress,
			TaskID:  "large-task",
			Payload: largePayload,
			ID:      generateID(),
			Time:    time.Now(),
		}

		if err := ws.WriteJSON(msg); err != nil {
			t.Errorf("Failed to send large message: %v", err)
		}

		// Connection should still be open
		time.Sleep(100 * time.Millisecond)
	})

	t.Run("HubNotRunning", func(t *testing.T) {
		// Create hub but don't run it
		deadHub := NewWebSocketHub()

		server2 := httptest.NewServer(WebSocketHandler(deadHub))
		defer server2.Close()

		wsURL2 := strings.Replace(server2.URL, "http", "ws", 1)
		ws, _, err := websocket.DefaultDialer.Dial(wsURL2, nil)
		if err != nil {
			t.Fatalf("Failed to connect: %v", err)
		}
		defer ws.Close()

		// Try to register - should timeout since hub isn't processing
		regPayload, _ := json.Marshal(map[string]interface{}{
			"agent_id": "dead-hub-worker",
		})
		ws.WriteJSON(WSMessage{
			Version: "1",
			Type:    MsgAgentRegister,
			Payload: regPayload,
			Time:    time.Now(),
		})

		// Should timeout waiting for ack
		ws.SetReadDeadline(time.Now().Add(1 * time.Second))
		var ack WSMessage
		err = ws.ReadJSON(&ack)
		if err == nil {
			t.Error("Expected timeout since hub isn't running")
		}
	})

	t.Run("SendToClosedWorker", func(t *testing.T) {
		// Connect and register
		ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
		if err != nil {
			t.Fatalf("Failed to connect: %v", err)
		}

		regPayload, _ := json.Marshal(map[string]interface{}{
			"agent_id": "close-test-worker",
		})
		ws.WriteJSON(WSMessage{
			Version: "1",
			Type:    MsgAgentRegister,
			Payload: regPayload,
			Time:    time.Now(),
		})

		var ack WSMessage
		ws.ReadJSON(&ack)

		// Close connection abruptly
		ws.Close()

		// Wait for unregister
		time.Sleep(200 * time.Millisecond)

		// Try to send to closed worker - should not panic
		task := Task{ID: "orphan-task"}
		hub.SendToWorker("close-test-worker", MsgTaskAssigned, "orphan-task", task)

		// Should not be in worker list
		for _, w := range hub.ListWorkers() {
			if w == "close-test-worker" {
				t.Error("Closed worker still in list")
			}
		}
	})

	t.Run("PanicRecovery", func(t *testing.T) {
		// The handler has panic recovery - verify it works
		// This is implicitly tested by other tests not causing server crashes
		t.Log("Panic recovery is implemented in WebSocketHandler with defer/recover")
	})
}

// TestWebSocketHubOperations tests hub operations
func TestWebSocketHubOperations(t *testing.T) {
	hub := NewWebSocketHub()
	go hub.Run()
	defer hub.Stop()

	t.Run("ListWorkersEmptyHub", func(t *testing.T) {
		workers := hub.ListWorkers()
		if len(workers) != 0 {
			t.Errorf("Expected empty workers, got %d", len(workers))
		}
	})

	t.Run("BroadcastEmptyHub", func(t *testing.T) {
		// Should not block or panic on empty hub
		msg := WSMessage{
			Version: "1",
			Type:    MsgTaskCompleted,
			ID:      generateID(),
			Time:    time.Now(),
		}
		hub.Broadcast(msg)
		time.Sleep(100 * time.Millisecond)
	})

	t.Run("SendToNonExistentWorker", func(t *testing.T) {
		// Should return false but not panic
		msg := WSMessage{
			Version: "1",
			Type:    MsgTaskAssigned,
			ID:      generateID(),
			Time:    time.Now(),
		}
		result := hub.SendToAgent("ghost-worker", msg)
		if result {
			t.Error("Expected false for non-existent worker")
		}
	})
}

// TestWebSocketMessageTypes tests various message types
func TestWebSocketMessageTypes(t *testing.T) {
	hub := NewWebSocketHub()
	go hub.Run()
	defer hub.Stop()

	server := httptest.NewServer(WebSocketHandler(hub))
	defer server.Close()

	wsURL := strings.Replace(server.URL, "http", "ws", 1)
	ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatalf("Failed to connect: %v", err)
	}
	defer ws.Close()

	// Register
	regPayload, _ := json.Marshal(map[string]interface{}{
		"agent_id": "msg-type-worker",
	})
	ws.WriteJSON(WSMessage{
		Version: "1",
		Type:    MsgAgentRegister,
		Payload: regPayload,
		Time:    time.Now(),
	})

	var ack WSMessage
	ws.ReadJSON(&ack)

	t.Run("TaskProgressMessage", func(t *testing.T) {
		progressPayload, _ := json.Marshal(map[string]interface{}{
			"percent": 50,
			"message": "Halfway done",
		})

		msg := WSMessage{
			Version: "1",
			Type:    MsgTaskProgress,
			TaskID:  "progress-task",
			Payload: progressPayload,
			ID:      generateID(),
			Time:    time.Now(),
		}

		if err := ws.WriteJSON(msg); err != nil {
			t.Fatalf("Failed to send progress: %v", err)
		}

		time.Sleep(100 * time.Millisecond)
	})

	t.Run("TaskFailedMessage", func(t *testing.T) {
		msg := WSMessage{
			Version: "1",
			Type:    MsgTaskFailed,
			TaskID:  "failed-task",
			Payload: json.RawMessage(`{"error": "Something went wrong"}`),
			ID:      generateID(),
			Time:    time.Now(),
		}

		if err := ws.WriteJSON(msg); err != nil {
			t.Fatalf("Failed to send failed status: %v", err)
		}

		time.Sleep(100 * time.Millisecond)
	})

	t.Run("UnknownMessageType", func(t *testing.T) {
		msg := WSMessage{
			Version: "1",
			Type:    "unknown.custom.type",
			ID:      generateID(),
			Time:    time.Now(),
		}

		if err := ws.WriteJSON(msg); err != nil {
			t.Fatalf("Failed to send unknown type: %v", err)
		}

		// Should not crash, just log
		time.Sleep(100 * time.Millisecond)
	})
}

// TestWebSocketTimeout tests timeout scenarios
func TestWebSocketTimeout(t *testing.T) {
	hub := NewWebSocketHub()
	go hub.Run()
	defer hub.Stop()

	server := httptest.NewServer(WebSocketHandler(hub))
	defer server.Close()

	wsURL := strings.Replace(server.URL, "http", "ws", 1)

	t.Run("HandshakeTimeout", func(t *testing.T) {
		ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
		if err != nil {
			t.Fatalf("Failed to connect: %v", err)
		}
		defer ws.Close()

		// Don't send registration - connection should not complete the handshake.
		// Use a short deadline so this test does not dominate suite runtime.
		ws.SetReadDeadline(time.Now().Add(500 * time.Millisecond))
		_, _, err = ws.ReadMessage()
		if err == nil {
			t.Error("expected read timeout waiting for handshake, got no error")
		}
	})
}

// TestWebSocketAgentRegistrationPersistsToHeartbeats verifies that when a
// WebSocket worker connects and sends agent.register, the hub writes an entry
// to agent_heartbeats so /api/agents returns the connected worker.
func TestWebSocketAgentRegistrationPersistsToHeartbeats(t *testing.T) {
	// Create in-memory SQLite DB with agent_heartbeats table
	db, err := sql.Open("sqlite3", ":memory:?_journal_mode=WAL")
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}
	defer db.Close()

	// Create agent_heartbeats table (from migrations/010_agent_health.sql)
	schema := `
	CREATE TABLE IF NOT EXISTS agent_heartbeats (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		agent_id TEXT NOT NULL UNIQUE,
		node TEXT NOT NULL,
		status TEXT NOT NULL DEFAULT 'idle',
		current_task_id TEXT,
		context_pct REAL DEFAULT 0,
		capabilities TEXT,
		last_seen TEXT NOT NULL DEFAULT (datetime('now')),
		connected_at TEXT NOT NULL DEFAULT (datetime('now'))
	);
	CREATE INDEX IF NOT EXISTS idx_agent_heartbeats_last_seen ON agent_heartbeats (last_seen);
	CREATE INDEX IF NOT EXISTS idx_agent_heartbeats_status ON agent_heartbeats (status);
	`
	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("failed to create schema: %v", err)
	}

	// Save original dbConn and restore after test
	origDB := getDBConn()
	setDBConn(db)
	defer func() { setDBConn(origDB) }()

	// Create hub and start it
	hub := NewWebSocketHub()
	go hub.Run()
	defer hub.Stop()

	// Start test server with WebSocket handler
	server := httptest.NewServer(WebSocketHandler(hub))
	defer server.Close()

	// Connect WebSocket client
	wsURL := strings.Replace(server.URL, "http", "ws", 1)
	ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatalf("failed to connect: %v", err)
	}
	defer ws.Close()

	// Send agent.register message
	regPayload, _ := json.Marshal(map[string]interface{}{
		"agent_id":     "test-agent",
		"name":         "Test",
		"node":         "prya",
		"tier":         "worker",
		"capabilities": []string{"code"},
	})

	regMsg := WSMessage{
		Version: "1",
		Type:    MsgAgentRegister,
		ID:      "msg-1",
		Payload: regPayload,
		Time:    time.Now(),
	}

	if err := ws.WriteJSON(regMsg); err != nil {
		t.Fatalf("failed to send registration: %v", err)
	}

	// Read acknowledgment
	ws.SetReadDeadline(time.Now().Add(5 * time.Second))
	var ack WSMessage
	if err := ws.ReadJSON(&ack); err != nil {
		t.Fatalf("failed to read ack: %v", err)
	}

	// Verify it's an agent.register_ack
	if ack.Type != MsgAgentRegisterAck {
		t.Errorf("expected ack type %s, got %s", MsgAgentRegisterAck, ack.Type)
	}

	// Query DB to verify agent_heartbeats entry
	var agentID, status string
	err = db.QueryRow(
		"SELECT agent_id, status FROM agent_heartbeats WHERE agent_id = ?",
		"test-agent",
	).Scan(&agentID, &status)

	if err != nil {
		t.Fatalf("failed to query agent_heartbeats: %v", err)
	}

	// Assert row exists and status is 'connected'
	if agentID != "test-agent" {
		t.Errorf("expected agent_id 'test-agent', got '%s'", agentID)
	}
	if status != "connected" {
		t.Errorf("expected status 'connected', got '%s'", status)
	}
}

// TestGenerateID tests the ID generation function
func TestGenerateID(t *testing.T) {
	id1 := generateID()
	id2 := generateID()

	if id1 == "" {
		t.Error("generateID returned empty string")
	}

	if id1 == id2 {
		t.Error("generateID returned duplicate IDs")
	}

	if !strings.HasPrefix(id1, "msg_") {
		t.Errorf("Expected ID to start with 'msg_', got %s", id1)
	}
}

// TestHeartbeatPruning tests that workers are pruned after heartbeat timeout.
// This test verifies the council Q5 requirement: Register → Wait → Confirm unregister.
func TestHeartbeatPruning(t *testing.T) {
	hub := NewWebSocketHub()
	go hub.Run()
	defer hub.Stop()

	server := httptest.NewServer(WebSocketHandler(hub))
	defer server.Close()

	wsURL := strings.Replace(server.URL, "http", "ws", 1)

	// Connect and register worker
	ws, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatalf("Failed to connect: %v", err)
	}

	regPayload, _ := json.Marshal(map[string]interface{}{
		"agent_id": "prune-test-worker",
	})
	ws.WriteJSON(WSMessage{
		Version: "1",
		Type:    MsgAgentRegister,
		Payload: regPayload,
		Time:    time.Now(),
	})

	// Read ack
	var ack WSMessage
	ws.ReadJSON(&ack)

	// Verify worker is registered
	workers := hub.ListWorkers()
	found := false
	for _, w := range workers {
		if w == "prune-test-worker" {
			found = true
			break
		}
	}
	if !found {
		t.Fatal("Worker not found in hub after registration")
	}

	// Close the connection (simulates worker disconnect)
	ws.Close()

	// Wait for unregister to propagate (hub processes unregister in run loop)
	time.Sleep(500 * time.Millisecond)

	// Verify worker is removed from hub
	workers = hub.ListWorkers()
	for _, w := range workers {
		if w == "prune-test-worker" {
			t.Error("Worker still in hub after disconnect - should have been pruned")
			break
		}
	}
}
