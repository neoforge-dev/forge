// Package websocket provides WebSocket client for real-time agent telemetry.
package websocket

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/url"
	"os"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// Client is a WebSocket client for real-time agent communication.
type Client struct {
	config   *Config
	conn     *websocket.Conn
	connMu   sync.Mutex
	handlers map[string]func([]byte)
	stopCh   chan struct{}
	wg       sync.WaitGroup

	// Connection state
	connected    bool
	retryCount   int
	lastConnect  time.Time
	lastError    error
	subscriptions []string
}

// NewClient creates a new WebSocket client.
func NewClient(config *Config) *Client {
	if config == nil {
		config = DefaultConfig("ws://localhost:8082", "unknown")
	}
	return &Client{
		config:       config,
		handlers:     make(map[string]func([]byte)),
		stopCh:       make(chan struct{}),
		subscriptions: []string{},
	}
}

// Connect establishes a WebSocket connection with retry logic.
func (c *Client) Connect(ctx context.Context) error {
	c.connMu.Lock()
	defer c.connMu.Unlock()

	if c.connected {
		return nil
	}

	// Parse URL
	u, err := url.Parse(c.config.URL)
	if err != nil {
		return fmt.Errorf("invalid WebSocket URL: %w", err)
	}

	// Build connection URL with agent ID
	q := u.Query()
	q.Set("agent_id", c.config.AgentID)
	u.RawQuery = q.Encode()

	// Attempt connection with retry
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-c.stopCh:
			return fmt.Errorf("client stopped")
		default:
		}

		dialer := websocket.DefaultDialer
		conn, _, err := dialer.Dial(u.String(), nil)
		if err != nil {
			c.lastError = err
			c.retryCount++

			if !c.config.Reconnect {
				return fmt.Errorf("connection failed: %w", err)
			}

			if c.config.MaxRetries > 0 && c.retryCount > c.config.MaxRetries {
				return fmt.Errorf("max retries exceeded (%d): %w", c.config.MaxRetries, err)
			}

			// Calculate backoff
			backoff := c.calculateBackoff()
			log.Printf("[WS] Connection failed (attempt %d), retrying in %v: %v", c.retryCount, backoff, err)

			select {
			case <-time.After(backoff):
				continue
			case <-ctx.Done():
				return ctx.Err()
			case <-c.stopCh:
				return fmt.Errorf("client stopped")
			}
		}

		// Success
		c.conn = conn
		c.connected = true
		c.lastConnect = time.Now()
		c.retryCount = 0
		c.lastError = nil

		// Re-subscribe to channels after reconnect
		c.resubscribeAll()

		// Start read loop
		c.wg.Add(1)
		go c.readLoop()

		// Start ping loop
		c.wg.Add(1)
		go c.pingLoop()

		log.Printf("[WS] Connected to %s", c.config.URL)
		return nil
	}
}

// calculateBackoff calculates exponential backoff duration.
func (c *Client) calculateBackoff() time.Duration {
	backoff := c.config.BackoffBase * time.Duration(1<<uint(c.retryCount-1))
	if backoff > c.config.BackoffMax {
		backoff = c.config.BackoffMax
	}
	return backoff
}

// SendTelemetry sends agent telemetry data.
func (c *Client) SendTelemetry(data *Telemetry) error {
	c.connMu.Lock()
	defer c.connMu.Unlock()

	if !c.connected || c.conn == nil {
		return fmt.Errorf("not connected")
	}

	// Set timestamp if not provided
	if data.Timestamp.IsZero() {
		data.Timestamp = time.Now()
	}

	// Set node if not provided
	if data.Node == "" {
		data.Node, _ = os.Hostname()
	}

	// Set agent ID if not provided
	if data.AgentID == "" {
		data.AgentID = c.config.AgentID
	}

	msg := Message{
		Type:    "telemetry",
		Payload: mustMarshal(data),
	}

	return c.sendMessage(msg)
}

// Subscribe registers a handler for an event type.
func (c *Client) Subscribe(event string, handler func([]byte)) {
	c.handlers[event] = handler

	// Store subscription for reconnection
	c.subscriptions = append(c.subscriptions, event)

	// If already connected, send subscribe message
	if c.connected {
		c.sendSubscribe(event)
	}
}

// sendSubscribe sends a subscription message.
func (c *Client) sendSubscribe(event string) error {
	msg := Message{
		Type: "subscribe",
		Payload: mustMarshal(map[string]string{
			"event": event,
		}),
	}
	return c.sendMessage(msg)
}

// resubscribeAll re-sends all subscriptions after reconnect.
func (c *Client) resubscribeAll() {
	for _, event := range c.subscriptions {
		c.sendSubscribe(event)
	}
}

// sendMessage sends a message with timeout.
func (c *Client) sendMessage(msg Message) error {
	if c.conn == nil {
		return fmt.Errorf("connection is nil")
	}

	// Set write deadline
	deadline := time.Now().Add(c.config.MessageTimeout)
	if err := c.conn.SetWriteDeadline(deadline); err != nil {
		return fmt.Errorf("set write deadline: %w", err)
	}

	data, err := json.Marshal(msg)
	if err != nil {
		return fmt.Errorf("marshal message: %w", err)
	}

	if err := c.conn.WriteMessage(websocket.TextMessage, data); err != nil {
		return fmt.Errorf("write message: %w", err)
	}

	return nil
}

// readLoop continuously reads messages from the WebSocket.
func (c *Client) readLoop() {
	defer c.wg.Done()
	defer c.handleDisconnect()

	for {
		select {
		case <-c.stopCh:
			return
		default:
		}

		c.connMu.Lock()
		conn := c.conn
		c.connMu.Unlock()

		if conn == nil {
			return
		}

		_, data, err := conn.ReadMessage()
		if err != nil {
			if !websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseAbnormalClosure) {
				log.Printf("[WS] Connection closed: %v", err)
			}
			return
		}

		// Parse message
		var msg Message
		if err := json.Unmarshal(data, &msg); err != nil {
			log.Printf("[WS] Failed to parse message: %v", err)
			continue
		}

		// Handle message by type
		switch msg.Type {
		case "ping":
			c.handlePing()
		case "event":
			c.handleEvent(msg.Payload)
		case "dispatch":
			c.handleEvent(msg.Payload)
		default:
			if handler, ok := c.handlers[msg.Type]; ok {
				handler(msg.Payload)
			}
		}
	}
}

// pingLoop sends periodic pings to keep connection alive.
func (c *Client) pingLoop() {
	defer c.wg.Done()

	// Skip if ping interval is zero
	if c.config.PingInterval <= 0 {
		return
	}

	ticker := time.NewTicker(c.config.PingInterval)
	defer ticker.Stop()

	for {
		select {
		case <-c.stopCh:
			return
		case <-ticker.C:
			c.connMu.Lock()
			connected := c.connected
			conn := c.conn
			c.connMu.Unlock()

			if !connected || conn == nil {
				return
			}

			msg := Message{Type: "ping"}
			if err := c.sendMessage(msg); err != nil {
				log.Printf("[WS] Ping failed: %v", err)
				return
			}
		}
	}
}

// handlePing responds to server ping.
func (c *Client) handlePing() {
	c.connMu.Lock()
	defer c.connMu.Unlock()

	if c.connected && c.conn != nil {
		msg := Message{Type: "pong"}
		c.sendMessage(msg)
	}
}

// handleEvent dispatches events to registered handlers.
func (c *Client) handleEvent(payload []byte) {
	var event Event
	if err := json.Unmarshal(payload, &event); err != nil {
		log.Printf("[WS] Failed to parse event: %v", err)
		return
	}

	if handler, ok := c.handlers[event.Name]; ok {
		handler(payload)
	}
}

// handleDisconnect handles connection loss with optional reconnect.
func (c *Client) handleDisconnect() {
	c.connMu.Lock()
	c.connected = false
	if c.conn != nil {
		c.conn.Close()
		c.conn = nil
	}
	c.connMu.Unlock()

	if c.config.Reconnect {
		log.Printf("[WS] Disconnected, attempting reconnect...")
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
		defer cancel()

		if err := c.Connect(ctx); err != nil {
			log.Printf("[WS] Reconnect failed: %v", err)
		}
	}
}

// Close gracefully closes the WebSocket connection.
func (c *Client) Close() error {
	close(c.stopCh)

	c.connMu.Lock()
	defer c.connMu.Unlock()

	if c.conn != nil {
		// Send close message
		err := c.conn.WriteMessage(websocket.CloseMessage,
			websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""))
		if err != nil {
			log.Printf("[WS] Error sending close message: %v", err)
		}
		c.conn.Close()
		c.conn = nil
		c.connected = false
	}

	// Wait for goroutines to finish
	c.wg.Wait()

	return nil
}

// IsConnected returns whether the client is currently connected.
func (c *Client) IsConnected() bool {
	c.connMu.Lock()
	defer c.connMu.Unlock()
	return c.connected
}

// Stats returns connection statistics.
func (c *Client) Stats() map[string]interface{} {
	c.connMu.Lock()
	defer c.connMu.Unlock()

	return map[string]interface{}{
		"connected":    c.connected,
		"url":          c.config.URL,
		"agent_id":     c.config.AgentID,
		"retry_count":  c.retryCount,
		"last_connect": c.lastConnect,
		"last_error":   c.lastError,
	}
}

// mustMarshal marshals to JSON or returns empty object on error.
func mustMarshal(v interface{}) json.RawMessage {
	data, err := json.Marshal(v)
	if err != nil {
		return json.RawMessage("{}")
	}
	return data
}
