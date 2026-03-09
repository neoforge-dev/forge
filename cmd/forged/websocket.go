//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// WebSocket message types (per ADR-011)
const (
	// Orchestrator -> Worker
	MsgAgentRegisterAck = "agent.register_ack"
	MsgTaskAssigned     = "task.assigned"
	MsgGenerateEnvelope = "generate_envelope"
	MsgBootstrap        = "bootstrap"
	MsgPing             = "ping"
	MsgTaskPause        = "task.pause"
	MsgTaskResume       = "task.resume"
	MsgTaskCancel       = "task.cancel"
	MsgError            = "error"

	// Worker -> Orchestrator
	MsgAgentRegister     = "agent.register"
	MsgTaskStarted       = "task.started"
	MsgTaskCompleted     = "task.completed"
	MsgTaskFailed        = "task.failed"
	MsgEnvelopeGenerated = "envelope.generated"
	MsgPong              = "pong"
	MsgTaskProgress      = "task.progress"
	MsgTaskComplete      = "task_complete" // Alternate completion type
)

// WSProtocolVersion is the current WebSocket protocol version.
// This must be incremented when breaking changes are made to the message schema.
const WSProtocolVersion = "1"

// WSMessage is the envelope for all WebSocket messages
type WSMessage struct {
	Version string          `json:"v"`                 // Protocol version: "1"
	Type    string          `json:"type"`              // Message type
	ID      string          `json:"id"`                // Message ID
	TaskID  string          `json:"task_id,omitempty"` // Associated task
	Payload json.RawMessage `json:"payload"`           // Type-specific payload
	Time    time.Time       `json:"ts"`                // Timestamp
}

// WebSocketWorker represents a connected worker
type WebSocketWorker struct {
	Conn         *websocket.Conn
	AgentID      string
	Node         string
	Tier         string
	Capabilities []string
	Send         chan WSMessage
	mu           sync.RWMutex
	lastPong     time.Time
}

// touchPong records a fresh heartbeat timestamp for this worker.
func (w *WebSocketWorker) touchPong() {
	w.mu.Lock()
	w.lastPong = time.Now()
	w.mu.Unlock()
}

// lastHeartbeat returns the last known heartbeat time for this worker.
func (w *WebSocketWorker) lastHeartbeat() time.Time {
	w.mu.RLock()
	defer w.mu.RUnlock()
	return w.lastPong
}

// WebSocketHub manages all WebSocket connections
type WebSocketHub struct {
	workers    map[string]*WebSocketWorker
	register   chan *registerRequest
	unregister chan *WebSocketWorker
	broadcast  chan WSMessage
	mu         sync.RWMutex
	quit       chan struct{}
}

// Hub is an alias used by the rest of the codebase for the WebSocket hub.
type Hub = WebSocketHub

// registerRequest wraps a worker registration with an optional completion
// signal so callers can wait until the hub's internal state is updated.
type registerRequest struct {
	worker *WebSocketWorker
	done   chan struct{}
}

// NewWebSocketHub creates a new WebSocket hub
func NewWebSocketHub() *WebSocketHub {
	return &WebSocketHub{
		workers:    make(map[string]*WebSocketWorker),
		register:   make(chan *registerRequest),
		unregister: make(chan *WebSocketWorker),
		broadcast:  make(chan WSMessage, 100),
		quit:       make(chan struct{}),
	}
}

// Stop signals the hub's Run() goroutine to exit. Safe to call multiple times.
func (h *WebSocketHub) Stop() {
	select {
	case <-h.quit:
		// already closed
	default:
		close(h.quit)
	}
}

// NewHub creates a new Hub instance (compatibility helper).
func NewHub() *Hub {
	return NewWebSocketHub()
}

// Run starts the hub event loop
func (h *WebSocketHub) Run() {
	// Periodic heartbeat pruning for stale workers.
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-h.quit:
			return

		case <-ticker.C:
			h.pruneStaleWorkers()

		case req := <-h.register:
			worker := req.worker
			h.mu.Lock()
			h.workers[worker.AgentID] = worker
			active := len(h.workers)
			h.mu.Unlock()
			log.Printf("[WebSocket] Agent registered: %s (node: %s)", worker.AgentID, worker.Node)
			log.Printf("[WebSocket] workers count after register: %d", active)

			// Update metrics with current active connections count.
			metrics.SetActiveConnections(int64(active))

			// Signal to the caller that registration is fully applied so
			// hub.ListWorkers() sees a consistent view.
			if req.done != nil {
				close(req.done)
			}

		case worker := <-h.unregister:
			h.mu.Lock()
			if _, ok := h.workers[worker.AgentID]; ok {
				delete(h.workers, worker.AgentID)
				close(worker.Send)
			}
			active := len(h.workers)
			h.mu.Unlock()
			log.Printf("[WebSocket] Agent unregistered: %s", worker.AgentID)

			// Mark agent as disconnected in DB so heartbeat reflects reality.
			if db := getDBConn(); db != nil {
				_, dbErr := db.Exec(
					`UPDATE agents SET status='disconnected', updated_at=? WHERE id=?`,
					time.Now().UTC().Format(time.RFC3339), worker.AgentID,
				)
				if dbErr != nil {
					log.Printf("WARN: failed to mark agent %s disconnected: %v", worker.AgentID, dbErr)
				}
			}

			// Keep metrics in sync with hub state.
			metrics.SetActiveConnections(int64(active))

		case msg := <-h.broadcast:
			h.mu.RLock()
			for agentID, worker := range h.workers {
				select {
				case worker.Send <- msg:
					// Message queued successfully
				default:
					// Worker send buffer full, log and skip
					log.Printf("[WebSocket] Send buffer full for %s, dropping message", agentID)
				}
			}
			h.mu.RUnlock()
		}
	}
}

// Broadcast sends a message to all connected workers
func (h *WebSocketHub) Broadcast(msg WSMessage) {
	h.broadcast <- msg
}

// SendToAgent sends a message to a specific agent
func (h *WebSocketHub) SendToAgent(agentID string, msg WSMessage) bool {
	h.mu.RLock()
	worker, ok := h.workers[agentID]
	h.mu.RUnlock()

	if !ok {
		return false
	}

	select {
	case worker.Send <- msg:
		return true
	default:
		return false
	}
}

// ListWorkers returns list of connected agent IDs
func (h *WebSocketHub) ListWorkers() []string {
	h.mu.RLock()
	defer h.mu.RUnlock()

	workers := make([]string, 0, len(h.workers))
	now := time.Now()
	for agentID, worker := range h.workers {
		// Only report workers that have an up-to-date heartbeat.
		if now.Sub(worker.lastHeartbeat()) <= 60*time.Second {
			workers = append(workers, agentID)
		}
	}
	return workers
}

// pruneStaleWorkers removes workers whose heartbeat has expired.
func (h *WebSocketHub) pruneStaleWorkers() {
	cutoff := time.Now().Add(-60 * time.Second)

	h.mu.Lock()
	defer h.mu.Unlock()

	// OPTIMIZATION: Collect stale workers first, then remove them
	// to avoid locking issues with worker.lastHeartbeat()
	var staleWorkers []*WebSocketWorker

	for agentID, worker := range h.workers {
		// Access lastPong directly while holding the lock (avoid double-lock)
		if worker.lastPong.Before(cutoff) {
			staleWorkers = append(staleWorkers, worker)
			delete(h.workers, agentID)
		}
	}

	// Close connections outside the lock
	for _, worker := range staleWorkers {
		log.Printf("[WebSocket] Pruning stale worker")
		close(worker.Send)
		worker.Conn.Close()
	}

	metrics.SetActiveConnections(int64(len(h.workers)))
}

var upgrader = websocket.Upgrader{
	ReadBufferSize:  1024,
	WriteBufferSize: 1024,
	CheckOrigin: func(r *http.Request) bool {
		return true // Allow all origins in development
	},
}

// WebSocketHandler handles WebSocket connections
func WebSocketHandler(hub *WebSocketHub) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Recover from any panics to prevent server crashes
		defer func() {
			if rec := recover(); rec != nil {
				log.Printf("[WebSocket] Recovered from panic: %v", rec)
			}
		}()

		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			log.Printf("[WebSocket] Upgrade failed: %v", err)
			return
		}
		// Note: conn.Close() is called by writePump/readPump, not here

		// Set read deadline for handshake
		conn.SetReadDeadline(time.Now().Add(10 * time.Second))

		// Wait for handshake message
		_, message, err := conn.ReadMessage()
		if err != nil {
			log.Printf("[WebSocket] Failed to read handshake: %v", err)
			return
		}

		// Clear read deadline after successful handshake read
		conn.SetReadDeadline(time.Time{})

		var handshake WSMessage
		if err := json.Unmarshal(message, &handshake); err != nil {
			log.Printf("[WebSocket] Invalid handshake JSON: %v", err)
			return
		}

		if handshake.Type != MsgAgentRegister {
			log.Printf("[WebSocket] Expected handshake, got: %s", handshake.Type)
			return
		}

		// Protocol version check
		if handshake.Version != WSProtocolVersion {
			errMsg := fmt.Sprintf("Please update your CLI: protocol version mismatch (client: %s, server: %s)", handshake.Version, WSProtocolVersion)
			log.Printf("[WebSocket] %s", errMsg)
			payload, _ := json.Marshal(map[string]string{
				"error":   "version_mismatch",
				"message": errMsg,
				"client_version":  handshake.Version,
				"server_version": WSProtocolVersion,
			})
			_ = conn.WriteJSON(WSMessage{
				Version: WSProtocolVersion,
				Type:    MsgError,
				ID:      generateID(),
				Payload: payload,
				Time:    time.Now(),
			})
			_ = conn.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(websocket.ClosePolicyViolation, errMsg))
			_ = conn.Close()
			return
		}

		// Parse registration payload
		var regPayload struct {
			AgentID      string   `json:"agent_id"`
			Name         string   `json:"name"`
			Node         string   `json:"node"`
			Tier         string   `json:"tier"`
			Capabilities []string `json:"capabilities"`
		}
		if err := json.Unmarshal(handshake.Payload, &regPayload); err != nil {
			log.Printf("[WebSocket] Invalid registration payload: %v", err)
			return
		}

		sendRegistrationError := func(reason string) {
			payload, _ := json.Marshal(map[string]string{"error": reason})
			_ = conn.WriteJSON(WSMessage{
				Version: "1",
				Type:    MsgError,
				ID:      generateID(),
				Payload: payload,
				Time:    time.Now(),
			})
			_ = conn.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(websocket.ClosePolicyViolation, reason))
			_ = conn.Close()
		}

		regPayload.AgentID = sanitizeID(regPayload.AgentID)
		if regPayload.AgentID == "" || !isValidID(regPayload.AgentID) {
			sendRegistrationError("invalid agent_id")
			return
		}

		// Validate capabilities array (allow empty, but validate each entry if present).
		for i := range regPayload.Capabilities {
			regPayload.Capabilities[i] = sanitizeID(regPayload.Capabilities[i])
			if regPayload.Capabilities[i] == "" || !isValidID(regPayload.Capabilities[i]) {
				sendRegistrationError("invalid capabilities")
				return
			}
		}

		// Create worker
		worker := &WebSocketWorker{
			Conn:         conn,
			AgentID:      regPayload.AgentID,
			Node:         regPayload.Node,
			Tier:         regPayload.Tier,
			Capabilities: regPayload.Capabilities,
			Send:         make(chan WSMessage, 100),
		}
		worker.touchPong()

		// Register with hub and wait until the hub has updated its internal
		// worker map so that subsequent calls to hub.ListWorkers() reflect
		// this connection immediately (e.g. in debug endpoints).
		req := &registerRequest{
			worker: worker,
			done:   make(chan struct{}),
		}
		hub.register <- req
		<-req.done

		// Persist registration to agent_heartbeats so /api/agents reflects connected workers.
		if db := getDBConn(); db != nil {
			caps := strings.Join(regPayload.Capabilities, ",")
			now := time.Now().UTC().Format("2006-01-02 15:04:05")
			_, err := db.Exec(`INSERT INTO agent_heartbeats
				(agent_id, node, status, context_pct, capabilities, last_seen, connected_at)
				VALUES (?, ?, 'connected', 0.0, ?, ?, ?)
				ON CONFLICT(agent_id) DO UPDATE SET
					node=excluded.node, status='connected',
					capabilities=excluded.capabilities, last_seen=excluded.last_seen`,
				regPayload.AgentID, regPayload.Node, caps, now, now)
			if err != nil {
				log.Printf("[WebSocket] Failed to upsert agent_heartbeats for %s: %v", regPayload.AgentID, err)
			}
		}

		// Send acknowledgment
		ackPayload, _ := json.Marshal(map[string]interface{}{
			"agent_id":        regPayload.AgentID,
			"capabilities":    regPayload.Capabilities,
			"connected_nodes": hub.ListWorkers(),
		})
		ack := WSMessage{
			Version: "1",
			Type:    MsgAgentRegisterAck,
			ID:      generateID(),
			Payload: ackPayload,
			Time:    time.Now(),
		}
		if err := conn.WriteJSON(ack); err != nil {
			log.Printf("[WebSocket] Failed to send ack: %v", err)
			hub.unregister <- worker
			conn.Close()
			return
		}

		// Start goroutines for reading and writing
		go worker.writePump(hub)
		go worker.readPump(hub)
	}
}

// writePump pumps messages from the hub to the WebSocket connection
func (w *WebSocketWorker) writePump(hub *WebSocketHub) {
	ticker := time.NewTicker(30 * time.Second) // Heartbeat every 30s
	defer func() {
		ticker.Stop()
		w.Conn.Close()
	}()

	for {
		select {
		case message, ok := <-w.Send:
			w.Conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if !ok {
				// Hub closed the channel
				w.Conn.WriteMessage(websocket.CloseMessage, []byte{})
				return
			}

			if err := w.Conn.WriteJSON(message); err != nil {
				log.Printf("[WebSocket] Write error for %s: %v", w.AgentID, err)
				return
			}

		case <-ticker.C:
			// Send websocket ping frame
			w.Conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if err := w.Conn.WriteMessage(websocket.PingMessage, []byte{}); err != nil {
				log.Printf("[WebSocket] Ping error for %s: %v", w.AgentID, err)
				return
			}

			// Check last pong
			if time.Since(w.lastHeartbeat()) > 60*time.Second {
				log.Printf("[WebSocket] Heartbeat timeout for %s", w.AgentID)
				return
			}
		}
	}
}

// readPump pumps messages from the WebSocket connection to the hub
func (w *WebSocketWorker) readPump(hub *WebSocketHub) {
	defer func() {
		hub.unregister <- w
		w.Conn.Close()
		// Mark agent offline in DB on disconnect.
		if db := getDBConn(); db != nil {
			now := time.Now().UTC().Format("2006-01-02 15:04:05")
			db.Exec(`UPDATE agent_heartbeats SET status='offline', last_seen=? WHERE agent_id=?`, now, w.AgentID)
		}
	}()

	w.Conn.SetReadLimit(512 * 1024) // 512KB max message size
	w.Conn.SetReadDeadline(time.Now().Add(60 * time.Second))
	w.Conn.SetPongHandler(func(string) error {
		w.touchPong()
		w.Conn.SetReadDeadline(time.Now().Add(60 * time.Second))
		return nil
	})

	for {
		var msg WSMessage
		err := w.Conn.ReadJSON(&msg)
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseAbnormalClosure) {
				log.Printf("[WebSocket] Read error for %s: %v", w.AgentID, err)
			}
			return
		}

		// Process message based on type
		switch msg.Type {
		case MsgTaskStarted:
			log.Printf("[WebSocket] Task %s started by %s", msg.TaskID, w.AgentID)
			// Update task status in database
			if taskQueue != nil {
				taskQueue.UpdateTaskStatus(msg.TaskID, "in_progress", w.AgentID)
			}

		case MsgTaskCompleted, MsgTaskComplete:
			log.Printf("[WebSocket] Task %s completed by %s", msg.TaskID, w.AgentID)
			if taskQueue != nil {
				// Try to extract result from payload
				var result string
				var payload map[string]interface{}
				if err := json.Unmarshal(msg.Payload, &payload); err == nil {
					if r, ok := payload["result"].(string); ok {
						result = r
					}
				}
				taskQueue.Complete(context.Background(), msg.TaskID, result)
			}

			// Release any lease on this task
			if db := getDBConn(); db != nil {
				db.Exec("DELETE FROM leases WHERE task_id = ?", msg.TaskID)
			}

			metrics.RecordTaskCompleted()
			metrics.RecordAgentTaskCompleted(w.AgentID, 0)
			// Broadcast to other workers
			hub.Broadcast(msg)

		case MsgTaskFailed:
			log.Printf("[WebSocket] Task %s failed by %s", msg.TaskID, w.AgentID)
			if taskQueue != nil {
				// Try to extract error from payload
				var errMsg string
				var payload map[string]interface{}
				if err := json.Unmarshal(msg.Payload, &payload); err == nil {
					if e, ok := payload["error"].(string); ok {
						errMsg = e
					}
				}
				taskQueue.Fail(context.Background(), msg.TaskID, errMsg)
			}

			// Release any lease on this task
			if db := getDBConn(); db != nil {
				db.Exec("DELETE FROM leases WHERE task_id = ?", msg.TaskID)
			}

			metrics.RecordTaskFailed()
			metrics.RecordAgentTaskFailed(w.AgentID, 0)

		case MsgTaskProgress:
			log.Printf("[WebSocket] Task %s progress from %s", msg.TaskID, w.AgentID)

		default:
			log.Printf("[WebSocket] Unknown message type from %s: %s", w.AgentID, msg.Type)
		}
	}
}

// SendToWorker sends a task-related message to a specific worker.
// It matches the expected Hub API used in main.go.
func (h *WebSocketHub) SendToWorker(workerID, msgType, taskID string, task Task) {
	payload, err := json.Marshal(task)
	if err != nil {
		log.Printf("[WebSocket] Failed to marshal task %s for %s: %v", taskID, workerID, err)
		return
	}

	msg := WSMessage{
		Version: "1",
		Type:    msgType,
		ID:      generateID(),
		TaskID:  taskID,
		Payload: payload,
		Time:    time.Now(),
	}

	if ok := h.SendToAgent(workerID, msg); !ok {
		log.Printf("[WebSocket] Failed to enqueue message for worker %s", workerID)
	}
}

// SendBootstrap sends a context envelope to an agent for auto-bootstrap on task assignment.
func (h *WebSocketHub) SendBootstrap(workerID, taskID string, envelope interface{}) {
	payload, err := json.Marshal(envelope)
	if err != nil {
		log.Printf("[WebSocket] Failed to marshal bootstrap envelope for %s: %v", workerID, err)
		return
	}
	msg := WSMessage{
		Version: "1",
		Type:    MsgBootstrap,
		ID:      generateID(),
		TaskID:  taskID,
		Payload: payload,
		Time:    time.Now(),
	}
	if ok := h.SendToAgent(workerID, msg); !ok {
		log.Printf("[WebSocket] Failed to send bootstrap to worker %s", workerID)
	} else {
		log.Printf("[WebSocket] Sent bootstrap to %s for task %s", workerID, taskID)
	}
}

// generateID creates a unique message ID
var generateIDCounter uint64

func generateID() string {
	seq := atomic.AddUint64(&generateIDCounter, 1)
	return fmt.Sprintf("msg_%d_%d", time.Now().UnixNano(), seq)
}
