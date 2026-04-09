//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// XNode directories - initialized in init() from environment
var (
	XNodeOutboxDir string
	XNodeInboxDir  string
	XNodeAcksDir   string
	xnodeCtx       context.Context
	xnodeCancel    context.CancelFunc
)

// StopXNode stops all XNode background workers
func StopXNode() {
	if xnodeCancel != nil {
		xnodeCancel()
		xnodeCancel = nil
	}
}

// initXNodeContext creates a cancellable context for XNode workers
// Only creates a new context if one doesn't exist
func initXNodeContext() context.Context {
	if xnodeCancel == nil {
		xnodeCtx, xnodeCancel = context.WithCancel(context.Background())
		return xnodeCtx
	}
	return xnodeCtx
}

func init() {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}
	XNodeOutboxDir = filepath.Join(forgeRoot, ".forge/xnode/lead-outbox")
	XNodeInboxDir  = filepath.Join(forgeRoot, ".forge/xnode/lead-inbox")
	XNodeAcksDir   = filepath.Join(forgeRoot, ".forge/xnode/acks")
}

// XNodeErrorCode represents XNode-specific error codes
type XNodeErrorCode string

const (
	XNodeErrNodeNotFound   XNodeErrorCode = "XNODE_NODE_NOT_FOUND"
	XNodeErrNodeOffline    XNodeErrorCode = "XNODE_NODE_OFFLINE"
	XNodeErrForwardFailed  XNodeErrorCode = "XNODE_FORWARD_FAILED"
	XNodeErrOutboxWrite    XNodeErrorCode = "XNODE_OUTBOX_WRITE_FAILED"
	XNodeErrInvalidRequest XNodeErrorCode = "XNODE_INVALID_REQUEST"
	XNodeErrDatabase       XNodeErrorCode = "XNODE_DATABASE_ERROR"
	XNodeErrStreaming      XNodeErrorCode = "XNODE_STREAMING_ERROR"
)

// XNodeError is a structured error for XNode operations
type XNodeError struct {
	Code    XNodeErrorCode `json:"code"`
	Message string         `json:"message"`
	Details string         `json:"details,omitempty"`
	Err     error          `json:"-"`
}

func (e *XNodeError) Error() string {
	if e.Err != nil {
		return fmt.Sprintf("xnode: %s: %v", e.Message, e.Err)
	}
	return fmt.Sprintf("xnode: %s", e.Message)
}

func (e *XNodeError) Unwrap() error {
	return e.Err
}

// Helper constructors for XNode errors
func newXNodeNodeNotFoundError(nodeID string, err error) *XNodeError {
	return &XNodeError{
		Code:    XNodeErrNodeNotFound,
		Message: fmt.Sprintf("node '%s' not found", nodeID),
		Details: "The target node is not registered in the XNode mesh",
		Err:     err,
	}
}

func newXNodeNodeOfflineError(nodeID string, status string) *XNodeError {
	return &XNodeError{
		Code:    XNodeErrNodeOffline,
		Message: fmt.Sprintf("node '%s' is %s", nodeID, status),
		Details: "The target node is not available for task forwarding",
	}
}

func newXNodeForwardFailedError(taskID string, err error) *XNodeError {
	return &XNodeError{
		Code:    XNodeErrForwardFailed,
		Message: fmt.Sprintf("failed to forward task '%s'", taskID),
		Details: "Database operation failed while creating xnode task record",
		Err:     err,
	}
}

func newXNodeOutboxWriteError(nodeID string, err error) *XNodeError {
	return &XNodeError{
		Code:    XNodeErrOutboxWrite,
		Message: fmt.Sprintf("failed to write to outbox for node '%s'", nodeID),
		Details: "File system operation failed while writing to lead-outbox",
		Err:     err,
	}
}

func newXNodeInvalidRequestError(field string, err error) *XNodeError {
	return &XNodeError{
		Code:    XNodeErrInvalidRequest,
		Message: fmt.Sprintf("invalid request: %s", field),
		Details: "The request body contains invalid or missing data",
		Err:     err,
	}
}

func newXNodeDatabaseError(operation string, err error) *XNodeError {
	return &XNodeError{
		Code:    XNodeErrDatabase,
		Message: fmt.Sprintf("database error during %s", operation),
		Details: "SQLite operation failed",
		Err:     err,
	}
}

// Node represents a node in the XNode mesh
type Node struct {
	ID            string    `json:"id"`
	Hostname      string    `json:"hostname"`
	Address       string    `json:"address"`
	Status        string    `json:"status"`
	LastHeartbeat time.Time `json:"last_heartbeat"`
	Version       string    `json:"version,omitempty"`
	GitCommit     string    `json:"git_commit,omitempty"`
}

// XNodeTask represents a task distributed across nodes
type XNodeTask struct {
	ID         string    `json:"id"`
	SourceNode string    `json:"source_node"`
	TargetNode string    `json:"target_node"`
	TaskID     string    `json:"task_id"`
	Status     string    `json:"status"`
	CreatedAt  time.Time `json:"created_at"`
	UpdatedAt  time.Time `json:"updated_at"`
}

// XNodeController manages cross-node communication
type XNodeController struct {
	db        *sql.DB
	mu        sync.RWMutex
	nodeID    string
	outboxDir string
}

// NewXNodeController creates a new XNodeController
func NewXNodeController(db *sql.DB, nodeID string) (*XNodeController, error) {
	if db == nil {
		return nil, &XNodeError{
			Code:    XNodeErrDatabase,
			Message: "database connection is nil",
		}
	}

	if nodeID == "" {
		hostname, err := os.Hostname()
		if err != nil {
			return nil, fmt.Errorf("xnode: failed to get hostname: %w", err)
		}
		nodeID = hostname
	}

	outboxDir := XNodeOutboxDir
	// Ensure outbox exists
	if err := os.MkdirAll(outboxDir, 0755); err != nil {
		return nil, fmt.Errorf("xnode: failed to create outbox directory: %w", err)
	}

	return &XNodeController{
		db:        db,
		nodeID:    nodeID,
		outboxDir: outboxDir,
	}, nil
}

// RegisterRoutes registers HTTP handlers with the provided mux
func (xc *XNodeController) RegisterRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/api/xnode/nodes", xc.ListNodesHandler)
	mux.HandleFunc("/api/xnode/nodes/register", xc.RegisterHandler)
	mux.HandleFunc("/api/xnode/nodes/", xc.DeleteNodeHandler)
	mux.HandleFunc("/api/xnode/forward", xc.ForwardHandler)
	mux.HandleFunc("/api/xnode/status", xc.StatusHandler)
	mux.HandleFunc("/api/xnode/events", xc.SSEDeliveryHandler)
	mux.HandleFunc("/api/xnode/inbox/", xc.ListInboxHandler)
	mux.HandleFunc("/api/xnode/inbox", xc.ListInboxHandler)
	mux.HandleFunc("/api/xnode/acks", xc.ListAcksHandler)
}

// StartHeartbeatMonitor starts a background loop to check for offline nodes
func (xc *XNodeController) StartHeartbeatMonitor(interval time.Duration) {
	// Ensure context is initialized before starting goroutine
	ctx := initXNodeContext()

	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()

		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				// Self-heartbeat: keep this node alive in the nodes table
				if err := xc.Heartbeat(ctx, xc.nodeID); err != nil {
					log.Printf("[XNode] self-heartbeat failed: %v", err)
				}
				// Mark nodes as offline if no heartbeat for 10 minutes
				threshold := time.Now().Add(-10 * time.Minute)
				_, err := xc.db.Exec(`
					UPDATE nodes SET status = 'offline'
					WHERE last_heartbeat < ? AND status = 'online'
				`, threshold)
				if err != nil {
					log.Printf("[XNode] Heartbeat monitor error: %v", err)
				}
			}
		}
	}()
}

// RegisterNode adds or updates a node in the registry
func (xc *XNodeController) RegisterNode(ctx context.Context, node Node) error {
	_, err := xc.db.ExecContext(ctx, `
		INSERT INTO nodes (id, hostname, address, status, last_heartbeat, version, git_commit)
		VALUES (?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			hostname = excluded.hostname,
			address = excluded.address,
			status = excluded.status,
			last_heartbeat = excluded.last_heartbeat,
			version = excluded.version,
			git_commit = excluded.git_commit
	`, node.ID, node.Hostname, node.Address, node.Status, node.LastHeartbeat, node.Version, node.GitCommit)
	if err != nil {
		return newXNodeDatabaseError("register node", err)
	}
	return nil
}

// Heartbeat updates the last heartbeat for a node, creating it if it doesn't exist
func (xc *XNodeController) Heartbeat(ctx context.Context, nodeID string) error {
	result, err := xc.db.ExecContext(ctx, `
		UPDATE nodes SET last_heartbeat = CURRENT_TIMESTAMP, status = 'online'
		WHERE id = ?
	`, nodeID)
	if err != nil {
		return newXNodeDatabaseError("heartbeat update", err)
	}

	rows, err := result.RowsAffected()
	if err != nil {
		return newXNodeDatabaseError("heartbeat rows affected", err)
	}
	if rows == 0 {
		// Node doesn't exist - insert it.
		// NODE_ADDR env var wins (set by forge-node-join.sh with Tailscale IP).
		nodeAddr := os.Getenv("NODE_ADDR")
		if nodeAddr == "" {
			// Try Tailscale IP for correct mesh-routable address; fall back to hostname.
			if ip := detectTailscaleIP(); ip != "" {
				nodeAddr = ip + ":8081"
			} else {
				nodeAddr = nodeID + ":8081"
			}
		}
		_, err := xc.db.ExecContext(ctx, `
			INSERT INTO nodes (id, hostname, address, status, last_heartbeat)
			VALUES (?, ?, ?, 'online', CURRENT_TIMESTAMP)
		`, nodeID, nodeID, nodeAddr)
		if err != nil {
			return newXNodeDatabaseError("heartbeat insert", err)
		}
	}

	return nil
}

// ForwardTask forwards a task to another node
func (xc *XNodeController) ForwardTask(ctx context.Context, targetNodeID string, taskID string) error {
	// 1. Verify target node is online
	var status string
	err := xc.db.QueryRowContext(ctx, "SELECT status FROM nodes WHERE id = ?", targetNodeID).Scan(&status)
	if err != nil {
		if err == sql.ErrNoRows {
			return newXNodeNodeNotFoundError(targetNodeID, err)
		}
		return newXNodeDatabaseError("query node status", err)
	}
	if status != "online" {
		return newXNodeNodeOfflineError(targetNodeID, status)
	}

	// 2. Create xnode_task record
	xnodeTaskID := generateULID()
	_, err = xc.db.ExecContext(ctx, `
		INSERT INTO xnode_tasks (id, source_node, target_node, task_id, status)
		VALUES (?, ?, ?, ?, ?)
	`, xnodeTaskID, xc.nodeID, targetNodeID, taskID, "forwarded")
	if err != nil {
		return newXNodeForwardFailedError(taskID, err)
	}

	// 3. Write to lead-outbox (JSONL format as per ADR-010)
	message := map[string]interface{}{
		"v":               "1.0",
		"message_id":      xnodeTaskID,
		"type":            "task_forward",
		"source":          xc.nodeID,
		"target":          targetNodeID,
		"payload":         map[string]string{"task_id": taskID},
		"ts":              time.Now().Format(time.RFC3339),
		"idempotency_key": fmt.Sprintf("xnode-forward-%s", xnodeTaskID),
	}

	data, err := json.Marshal(message)
	if err != nil {
		return newXNodeOutboxWriteError(targetNodeID, fmt.Errorf("marshal message: %w", err))
	}

	outboxPath := filepath.Join(xc.outboxDir, targetNodeID+".jsonl")
	f, err := os.OpenFile(outboxPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return newXNodeOutboxWriteError(targetNodeID, fmt.Errorf("open file: %w", err))
	}
	defer f.Close()

	if _, err := f.Write(append(data, '\n')); err != nil {
		return newXNodeOutboxWriteError(targetNodeID, fmt.Errorf("write data: %w", err))
	}

	return nil
}

// HTTP Handlers

func (xc *XNodeController) ListInboxHandler(w http.ResponseWriter, r *http.Request) {
	nodeID := strings.TrimPrefix(r.URL.Path, "/api/xnode/inbox/")
	if nodeID == "/api/xnode/inbox" {
		nodeID = ""
	}
	// Reject node IDs containing path separators or traversal sequences.
	if strings.Contains(nodeID, "/") || strings.Contains(nodeID, "..") || strings.Contains(nodeID, "\\") {
		http.Error(w, "invalid node id", http.StatusBadRequest)
		return
	}

	var allMessages []map[string]interface{}

	entries, err := os.ReadDir(XNodeInboxDir)
	if err != nil {
		if os.IsNotExist(err) {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"messages": allMessages,
				"count":    0,
			})
			return
		}
		respondWithError(w, http.StatusInternalServerError, "Failed to read inbox", err.Error())
		return
	}

	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".jsonl") {
			continue
		}

		currentNode := strings.TrimSuffix(entry.Name(), ".jsonl")
		if nodeID != "" && currentNode != nodeID {
			continue
		}

		filePath := filepath.Join(XNodeInboxDir, entry.Name())
		messages, err := xc.readNewMessages(filePath, 0)
		if err != nil {
			log.Printf("[XNode] Error reading inbox for %s: %v", currentNode, err)
			continue
		}
		allMessages = append(allMessages, messages...)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"messages": allMessages,
		"count":    len(allMessages),
	})
}

func (xc *XNodeController) ListAcksHandler(w http.ResponseWriter, r *http.Request) {
	var allAcks []map[string]interface{}

	entries, err := os.ReadDir(XNodeAcksDir)
	if err != nil {
		if os.IsNotExist(err) {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"acks":  allAcks,
				"count": 0,
			})
			return
		}
		respondWithError(w, http.StatusInternalServerError, "Failed to read acks", err.Error())
		return
	}

	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}

		filePath := filepath.Join(XNodeAcksDir, entry.Name())
		data, err := os.ReadFile(filePath)
		if err != nil {
			log.Printf("[XNode] Error reading ack %s: %v", entry.Name(), err)
			continue
		}

		var ack map[string]interface{}
		if err := json.Unmarshal(data, &ack); err != nil {
			log.Printf("[XNode] Error unmarshaling ack %s: %v", entry.Name(), err)
			continue
		}
		allAcks = append(allAcks, ack)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"acks":  allAcks,
		"count": len(allAcks),
	})
}

func (xc *XNodeController) RegisterHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		respondWithError(w, http.StatusMethodNotAllowed, "Method not allowed",
			"Only POST requests are supported for node registration")
		return
	}

	var node Node
	if err := json.NewDecoder(r.Body).Decode(&node); err != nil {
		respondWithError(w, http.StatusBadRequest, "Invalid request body",
			fmt.Sprintf("JSON decode failed: %v", err))
		return
	}

	if node.ID == "" {
		respondWithError(w, http.StatusBadRequest, "Missing required field",
			"Node ID is required for registration")
		return
	}

	node.LastHeartbeat = time.Now()
	if node.Status == "" {
		node.Status = "online"
	}

	if err := xc.RegisterNode(r.Context(), node); err != nil {
		if xerr, ok := isXNodeError(err); ok {
			respondWithXNodeError(w, xerr)
			return
		}
		respondWithError(w, http.StatusInternalServerError, "Registration failed",
			fmt.Sprintf("Failed to register node: %v", err))
		return
	}

	w.WriteHeader(http.StatusCreated)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(node)
}

func (xc *XNodeController) ListNodesHandler(w http.ResponseWriter, r *http.Request) {
	rows, err := xc.db.QueryContext(r.Context(), `
		SELECT id, hostname, address, status, last_heartbeat,
		       COALESCE(version, ''), COALESCE(git_commit, '')
		FROM nodes`)
	if err != nil {
		respondWithError(w, http.StatusInternalServerError, "Database error",
			fmt.Sprintf("Failed to query nodes: %v", err))
		return
	}
	defer rows.Close()

	var nodes []Node
	for rows.Next() {
		var n Node
		var lh string
		if err := rows.Scan(&n.ID, &n.Hostname, &n.Address, &n.Status, &lh, &n.Version, &n.GitCommit); err != nil {
			respondWithError(w, http.StatusInternalServerError, "Database error",
				fmt.Sprintf("Failed to scan node row: %v", err))
			return
		}
		n.LastHeartbeat, _ = time.Parse(time.RFC3339, lh) // SQLite might return different formats
		nodes = append(nodes, n)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(nodes)
}

// DeleteNodeHandler handles DELETE /api/xnode/nodes/{id} — removes a node from the registry.
func (xc *XNodeController) DeleteNodeHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodDelete {
		respondWithError(w, http.StatusMethodNotAllowed, "Method not allowed",
			"Only DELETE requests are supported for node removal")
		return
	}

	// Extract node ID from path: /api/xnode/nodes/{id}
	nodeID := strings.TrimPrefix(r.URL.Path, "/api/xnode/nodes/")
	nodeID = strings.TrimSpace(nodeID)
	if nodeID == "" {
		respondWithError(w, http.StatusBadRequest, "Missing node ID",
			"Node ID is required in the path: DELETE /api/xnode/nodes/{id}")
		return
	}

	result, err := xc.db.ExecContext(r.Context(), `DELETE FROM nodes WHERE id = ?`, nodeID)
	if err != nil {
		respondWithError(w, http.StatusInternalServerError, "Database error",
			fmt.Sprintf("Failed to delete node %q: %v", nodeID, err))
		return
	}

	rows, _ := result.RowsAffected()
	if rows == 0 {
		respondWithError(w, http.StatusNotFound, "Node not found",
			fmt.Sprintf("Node %q is not registered in the XNode mesh. Check: forge node list", nodeID))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status":  "ok",
		"deleted": nodeID,
	})
}

func (xc *XNodeController) ForwardHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		respondWithError(w, http.StatusMethodNotAllowed, "Method not allowed",
			"Only POST requests are supported for task forwarding")
		return
	}

	var req struct {
		TargetNode string `json:"target_node"`
		TaskID     string `json:"task_id"`
		Summary    string `json:"summary"`
		Durable    bool   `json:"durable"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		log.Printf("[XNode] ForwardHandler: JSON decode error: %v", err)
		respondWithError(w, http.StatusBadRequest, "Invalid request body",
			fmt.Sprintf("JSON decode failed: %v", err))
		return
	}

	if req.TargetNode == "" {
		respondWithError(w, http.StatusBadRequest, "Missing required field",
			"target_node is required")
		return
	}
	if req.TaskID == "" {
		respondWithError(w, http.StatusBadRequest, "Missing required field",
			"task_id is required")
		return
	}

	if err := xc.ForwardTask(r.Context(), req.TargetNode, req.TaskID); err != nil {
		if xerr, ok := isXNodeError(err); ok {
			// Map XNode error codes to HTTP status codes
			status := http.StatusInternalServerError
			switch xerr.Code {
			case XNodeErrNodeNotFound:
				status = http.StatusNotFound
			case XNodeErrNodeOffline:
				status = http.StatusServiceUnavailable
			case XNodeErrInvalidRequest:
				status = http.StatusBadRequest
			}
			respondWithXNodeError(w, xerr, status)
			return
		}
		respondWithError(w, http.StatusInternalServerError, "Forward failed",
			fmt.Sprintf("Failed to forward task: %v", err))
		return
	}

	w.WriteHeader(http.StatusAccepted)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status": "forwarded",
		"target": req.TargetNode,
	})
}

func (xc *XNodeController) StatusHandler(w http.ResponseWriter, r *http.Request) {
	var totalNodes, onlineNodes int
	if err := xc.db.QueryRow("SELECT COUNT(*) FROM nodes").Scan(&totalNodes); err != nil {
		respondWithError(w, http.StatusInternalServerError, "Database error",
			fmt.Sprintf("Failed to count nodes: %v", err))
		return
	}
	if err := xc.db.QueryRow("SELECT COUNT(*) FROM nodes WHERE status = 'online'").Scan(&onlineNodes); err != nil {
		respondWithError(w, http.StatusInternalServerError, "Database error",
			fmt.Sprintf("Failed to count online nodes: %v", err))
		return
	}

	var totalTasks int
	if err := xc.db.QueryRow("SELECT COUNT(*) FROM xnode_tasks").Scan(&totalTasks); err != nil {
		respondWithError(w, http.StatusInternalServerError, "Database error",
			fmt.Sprintf("Failed to count tasks: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"node_id":      xc.nodeID,
		"nodes_total":  totalNodes,
		"nodes_online": onlineNodes,
		"tasks_total":  totalTasks,
		"timestamp":    time.Now(),
	})
}

// SSEDeliveryHandler streams delivery status updates via Server-Sent Events
func (xc *XNodeController) SSEDeliveryHandler(w http.ResponseWriter, r *http.Request) {
	// Set SSE headers
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	// Get optional target_node filter from query params
	targetNodeFilter := r.URL.Query().Get("target_node")

	// Create a channel for client disconnect detection
	ctx := r.Context()

	// Create a ticker for heartbeat (30 seconds)
	heartbeatTicker := time.NewTicker(30 * time.Second)
	defer heartbeatTicker.Stop()

	// Create a ticker for checking outbox updates (1 second; override via FORGE_XNODE_CHECK_TICK_MS for tests)
	xnodeCheckInterval := 1 * time.Second
	if v := os.Getenv("FORGE_XNODE_CHECK_TICK_MS"); v != "" {
		if ms, err := strconv.Atoi(v); err == nil && ms > 0 {
			xnodeCheckInterval = time.Duration(ms) * time.Millisecond
		}
	}
	checkTicker := time.NewTicker(xnodeCheckInterval)
	defer checkTicker.Stop()

	// Track last seen message IDs per target node
	lastSeen := make(map[string]int64)

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming unsupported", http.StatusInternalServerError)
		return
	}

	// Send initial connection event
	fmt.Fprintf(w, "event: connected\n")
	fmt.Fprintf(w, "data: {\"status\":\"connected\",\"node_id\":\"%s\"}\n\n", xc.nodeID)
	flusher.Flush()

	log.Printf("[XNode] SSE client connected for delivery events")

	for {
		select {
		case <-ctx.Done():
			// Client disconnected
			log.Printf("[XNode] SSE client disconnected")
			return

		case <-heartbeatTicker.C:
			// Send heartbeat to keep connection alive
			fmt.Fprintf(w, "event: heartbeat\n")
			fmt.Fprintf(w, "data: {\"timestamp\":\"%s\"}\n\n", time.Now().Format(time.RFC3339))
			flusher.Flush()

		case <-checkTicker.C:
			// Check for new messages in outbox
			entries, err := os.ReadDir(xc.outboxDir)
			if err != nil {
				log.Printf("[XNode] Error reading outbox: %v", err)
				continue
			}

			for _, entry := range entries {
				if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".jsonl") {
					continue
				}

				targetNode := strings.TrimSuffix(entry.Name(), ".jsonl")

				// Apply filter if specified
				if targetNodeFilter != "" && targetNode != targetNodeFilter {
					continue
				}

				// Read file and find new messages
				filePath := filepath.Join(xc.outboxDir, entry.Name())
				messages, err := xc.readNewMessages(filePath, lastSeen[targetNode])
				if err != nil {
					log.Printf("[XNode] Error reading messages: %v", err)
					continue
				}

				// Update last seen position
				if len(messages) > 0 {
					fileInfo, _ := os.Stat(filePath)
					if fileInfo != nil {
						lastSeen[targetNode] = fileInfo.Size()
					}
				}

				// Send events for new messages
				for _, msg := range messages {
					event := map[string]interface{}{
						"message_id": msg["message_id"],
						"type":       msg["type"],
						"source":     msg["source"],
						"target":     targetNode,
						"status":     "delivered",
						"timestamp":  time.Now().Format(time.RFC3339),
					}

					data, _ := json.Marshal(event)
					fmt.Fprintf(w, "event: delivery\n")
					fmt.Fprintf(w, "data: %s\n\n", string(data))
				}

				if len(messages) > 0 {
					flusher.Flush()
				}
			}
		}
	}
}

// readNewMessages reads messages from a JSONL file starting from a byte offset
func (xc *XNodeController) readNewMessages(filePath string, startOffset int64) ([]map[string]interface{}, error) {
	file, err := os.Open(filePath)
	if err != nil {
		return nil, fmt.Errorf("xnode: open file: %w", err)
	}
	defer file.Close()

	// Seek to start offset
	if startOffset > 0 {
		_, err = file.Seek(startOffset, 0)
		if err != nil {
			return nil, fmt.Errorf("xnode: seek file: %w", err)
		}
	}

	var messages []map[string]interface{}
	scanner := bufio.NewScanner(file)
	lineNum := 0

	for scanner.Scan() {
		lineNum++
		line := scanner.Text()
		if line == "" {
			continue
		}

		var msg map[string]interface{}
		if err := json.Unmarshal([]byte(line), &msg); err != nil {
			log.Printf("[XNode] Warning: skipping invalid JSON at line %d in %s: %v", lineNum, filePath, err)
			continue // Skip invalid lines
		}
		messages = append(messages, msg)
	}

	if err := scanner.Err(); err != nil {
		return messages, fmt.Errorf("xnode: scan file: %w", err)
	}

	return messages, nil
}

// StartInboxWorker polls lead-inbox/ every 5s, processes new JSONL messages,
// and writes acks. This is the ingestion side of ADR-023 cross-node messaging.
func (xc *XNodeController) StartInboxWorker(ctx context.Context) {
	go func() {
		// Track file read offsets in memory: filename → byte offset
		offsets := make(map[string]int64)
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				xc.processInbox(offsets)
			}
		}
	}()
}

// processInbox scans all *.jsonl files in the inbox directory and routes new messages.
func (xc *XNodeController) processInbox(offsets map[string]int64) {
	entries, err := os.ReadDir(XNodeInboxDir)
	if err != nil {
		if !os.IsNotExist(err) {
			log.Printf("[XNode] processInbox: failed to read inbox dir %s: %v", XNodeInboxDir, err)
		}
		return
	}

	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".jsonl") {
			continue
		}

		filename := entry.Name()
		filePath := filepath.Join(XNodeInboxDir, filename)

		messages, err := xc.readNewMessages(filePath, offsets[filename])
		if err != nil {
			log.Printf("[XNode] processInbox: error reading %s: %v", filename, err)
			continue
		}

		if len(messages) == 0 {
			continue
		}

		// Advance offset to current file size
		info, err := os.Stat(filePath)
		if err == nil {
			offsets[filename] = info.Size()
		}

		ctx := context.Background()
		for _, msg := range messages {
			if routeErr := xc.routeMessage(ctx, msg); routeErr != nil {
				log.Printf("[XNode] processInbox: routeMessage error: %v", routeErr)
			}
		}
	}
}

// routeMessage dispatches a single inbox message based on its type and writes an ack.
func (xc *XNodeController) routeMessage(ctx context.Context, msg map[string]interface{}) error {
	msgType, _ := msg["type"].(string)
	messageID, _ := msg["message_id"].(string)
	if messageID == "" {
		messageID = generateULID()
	}

	switch msgType {
	case "task_forward":
		// Extract task_id from payload
		var taskID string
		if payload, ok := msg["payload"].(map[string]interface{}); ok {
			taskID, _ = payload["task_id"].(string)
		}
		if taskID == "" {
			log.Printf("[XNode] routeMessage: task_forward missing task_id, message_id=%s", messageID)
		} else {
			newID := generateULID()
			title := fmt.Sprintf("XNode forwarded task: %s", taskID)
			_, err := xc.db.ExecContext(ctx, `
				INSERT INTO tasks (id, status, state, domain, project, type, priority, title)
				VALUES (?, 'queued', 'QUEUED', 'xnode', 'forwarded', 'xnode_forward', 5, ?)
			`, newID, title)
			if err != nil {
				log.Printf("[XNode] routeMessage: failed to insert task record for task_id=%s: %v", taskID, err)
			} else {
				log.Printf("[XNode] routeMessage: queued forwarded task task_id=%s as %s", taskID, newID)
			}
		}

	case "heartbeat_update":
		sourceNodeID, _ := msg["source"].(string)
		if sourceNodeID == "" {
			log.Printf("[XNode] routeMessage: heartbeat_update missing source, message_id=%s", messageID)
		} else {
			if err := xc.Heartbeat(ctx, sourceNodeID); err != nil {
				log.Printf("[XNode] routeMessage: Heartbeat(%s) error: %v", sourceNodeID, err)
			} else {
				log.Printf("[XNode] routeMessage: heartbeat updated for node %s", sourceNodeID)
			}
		}

	default:
		log.Printf("[XNode] routeMessage: unknown type %q, message_id=%s — acking anyway", msgType, messageID)
	}

	// Write ack file regardless of routing outcome
	ackPath := filepath.Join(XNodeAcksDir, messageID+".json")
	ack := map[string]interface{}{
		"message_id": messageID,
		"status":     "acked",
		"ts":         time.Now().Format(time.RFC3339),
	}
	ackData, err := json.Marshal(ack)
	if err != nil {
		return fmt.Errorf("xnode: marshal ack for %s: %w", messageID, err)
	}
	if err := os.MkdirAll(XNodeAcksDir, 0755); err != nil {
		return fmt.Errorf("xnode: create acks dir: %w", err)
	}
	if err := os.WriteFile(ackPath, ackData, 0644); err != nil {
		return fmt.Errorf("xnode: write ack %s: %w", ackPath, err)
	}
	return nil
}

// HTTP response helpers

type errorResponse struct {
	Error   string `json:"error"`
	Message string `json:"message,omitempty"`
	Details string `json:"details,omitempty"`
	Code    string `json:"code,omitempty"`
}

// respondWithError sends a structured error response
func respondWithError(w http.ResponseWriter, status int, message, details string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(errorResponse{
		Error:   http.StatusText(status),
		Message: message,
		Details: details,
	})
}

// respondWithXNodeError sends an XNodeError as an HTTP response
func respondWithXNodeError(w http.ResponseWriter, xerr *XNodeError, status ...int) {
	statusCode := http.StatusInternalServerError
	if len(status) > 0 {
		statusCode = status[0]
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	json.NewEncoder(w).Encode(errorResponse{
		Error:   http.StatusText(statusCode),
		Message: xerr.Message,
		Details: xerr.Details,
		Code:    string(xerr.Code),
	})
}

// isXNodeError checks if an error is an XNodeError
func isXNodeError(err error) (*XNodeError, bool) {
	var xerr *XNodeError
	if errors.As(err, &xerr) {
		return xerr, true
	}
	return nil, false
}

// StartSerializationWorker starts a background goroutine that polls xnode_outbox
// and serializes pending messages to JSONL files.
func (xc *XNodeController) StartSerializationWorker(ctx context.Context, db *sql.DB) {
	go func() {
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()

		for {
			select {
			case <-ticker.C:
				xc.serializePendingMessages(ctx, db)
			case <-ctx.Done():
				log.Printf("[XNode] Serialization worker stopped")
				return
			}
		}
	}()
	log.Printf("[XNode] Serialization worker started (10s interval)")
}

// serializePendingMessages polls xnode_outbox for pending messages and writes them to JSONL files.
func (xc *XNodeController) serializePendingMessages(ctx context.Context, db *sql.DB) {
	rows, err := db.QueryContext(ctx, `
		SELECT id, target_node, message_type, payload, idempotency_key
		FROM xnode_outbox
		WHERE status = 'pending'
		ORDER BY created_at ASC
	`)
	if err != nil {
		log.Printf("[XNode] Error querying pending messages: %v", err)
		return
	}
	defer rows.Close()

	var messages []struct {
		ID             string
		TargetNode     string
		MessageType    string
		Payload        string
		IdempotencyKey sql.NullString
	}

	for rows.Next() {
		var m struct {
			ID             string
			TargetNode     string
			MessageType    string
			Payload        string
			IdempotencyKey sql.NullString
		}
		if err := rows.Scan(&m.ID, &m.TargetNode, &m.MessageType, &m.Payload, &m.IdempotencyKey); err != nil {
			log.Printf("[XNode] Error scanning pending message: %v", err)
			continue
		}
		messages = append(messages, m)
	}
	rows.Close()

	if len(messages) == 0 {
		return
	}

	// Group messages by target node
	messagesByNode := make(map[string][]struct {
		ID             string
		TargetNode     string
		MessageType    string
		Payload        string
		IdempotencyKey sql.NullString
	})
	for _, m := range messages {
		messagesByNode[m.TargetNode] = append(messagesByNode[m.TargetNode], m)
	}

	// Process each target node's messages
	for targetNode, nodeMessages := range messagesByNode {
		outboxPath := filepath.Join(xc.outboxDir, targetNode+".jsonl")
		f, err := os.OpenFile(outboxPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err != nil {
			log.Printf("[XNode] Error opening outbox file for %s: %v", targetNode, err)
			continue
		}

		var serializedCount int
		for _, m := range nodeMessages {
			// Build the JSONL message
			message := map[string]interface{}{
				"v":            "1.0",
				"message_id":   m.ID,
				"type":         m.MessageType,
				"source":       xc.nodeID,
				"target":       m.TargetNode,
				"ts":           time.Now().Format(time.RFC3339),
			}

			// Parse and add payload
			var payload map[string]interface{}
			if err := json.Unmarshal([]byte(m.Payload), &payload); err != nil {
				log.Printf("[XNode] Error unmarshaling payload for message %s: %v", m.ID, err)
				continue
			}
			message["payload"] = payload

			// Add idempotency key if present
			if m.IdempotencyKey.Valid {
				message["idempotency_key"] = m.IdempotencyKey.String
			}

			data, err := json.Marshal(message)
			if err != nil {
				log.Printf("[XNode] Error marshaling message %s: %v", m.ID, err)
				continue
			}

			if _, err := f.Write(append(data, '\n')); err != nil {
				log.Printf("[XNode] Error writing message %s to file: %v", m.ID, err)
				continue
			}

			// Update status to 'serialized'
			_, err = db.ExecContext(ctx, `
				UPDATE xnode_outbox
				SET status = 'serialized', serialized_at = datetime('now')
				WHERE id = ?
			`, m.ID)
			if err != nil {
				log.Printf("[XNode] Error updating status for message %s: %v", m.ID, err)
				continue
			}

			serializedCount++
		}

		f.Close()

		if serializedCount > 0 {
			log.Printf("[XNode] Serialized %d messages to %s.jsonl", serializedCount, targetNode)
		}
	}
}

// IngestIncomingMessages reads .forge/xnode/lead-inbox/*.jsonl files on startup
// and inserts them into xnode_inbox with idempotency check.
func (xc *XNodeController) IngestIncomingMessages(ctx context.Context, db *sql.DB) error {
	entries, err := os.ReadDir(XNodeInboxDir)
	if err != nil {
		if os.IsNotExist(err) {
			// Inbox directory doesn't exist yet, that's ok
			return nil
		}
		return fmt.Errorf("xnode: read inbox dir: %w", err)
	}

	var totalIngested int
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".jsonl") {
			continue
		}

		sourceNode := strings.TrimSuffix(entry.Name(), ".jsonl")
		filePath := filepath.Join(XNodeInboxDir, entry.Name())

		messages, err := xc.readNewMessages(filePath, 0)
		if err != nil {
			log.Printf("[XNode] Error reading inbox file %s: %v", entry.Name(), err)
			continue
		}

		var ingested int
		for _, msg := range messages {
			// Extract message fields
			msgID, _ := msg["message_id"].(string)
			if msgID == "" {
				msgID = generateULID()
			}

			msgType, _ := msg["type"].(string)
			if msgType == "" {
				msgType = "unknown"
			}

			idempotencyKey, _ := msg["idempotency_key"].(string)
			if idempotencyKey == "" {
				idempotencyKey = fmt.Sprintf("xnode-%s-%s", sourceNode, msgID)
			}

			// Marshal payload (everything except metadata)
			payload := make(map[string]interface{})
			for k, v := range msg {
				if k != "v" && k != "message_id" && k != "type" && k != "source" && 
				   k != "target" && k != "ts" && k != "idempotency_key" {
					payload[k] = v
				}
			}
			payloadJSON, _ := json.Marshal(payload)

			// Insert into xnode_inbox with idempotency check
			_, err := db.ExecContext(ctx, `
				INSERT OR IGNORE INTO xnode_inbox (id, source_node, message_type, payload, idempotency_key)
				VALUES (?, ?, ?, ?, ?)
			`, msgID, sourceNode, msgType, string(payloadJSON), idempotencyKey)
			if err != nil {
				log.Printf("[XNode] Error inserting message %s into inbox: %v", msgID, err)
				continue
			}

			ingested++
		}

		if ingested > 0 {
			log.Printf("[XNode] Ingested %d messages from %s", ingested, entry.Name())
			totalIngested += ingested
		}
	}

	if totalIngested > 0 {
		log.Printf("[XNode] Total ingested %d messages from inbox", totalIngested)
	}

	return nil
}
