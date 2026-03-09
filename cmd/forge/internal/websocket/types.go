// Package websocket provides WebSocket client for real-time agent telemetry.
package websocket

import (
	"encoding/json"
	"time"
)

// Telemetry represents agent telemetry data sent via WebSocket.
type Telemetry struct {
	AgentID    string    `json:"agent_id"`
	Status     string    `json:"status"`      // idle, busy, error
	ContextPct int       `json:"context_pct"` // 0-100
	TaskID     string    `json:"task_id,omitempty"`
	Timestamp  time.Time `json:"timestamp"`
	Node       string    `json:"node"`
}

// Message represents a WebSocket message.
type Message struct {
	Type    string          `json:"type"`    // telemetry, event, dispatch, ping, pong
	Payload json.RawMessage `json:"payload"` // depends on type
}

// Event represents an event received from the server.
type Event struct {
	Name      string                 `json:"name"`      // task_dispatch, config_update, etc.
	Data      map[string]interface{} `json:"data"`
	Timestamp time.Time              `json:"timestamp"`
}

// Config is the WebSocket client configuration.
type Config struct {
	URL            string        // WebSocket server URL (ws://host:port)
	AgentID        string        // Agent identifier
	Reconnect      bool          // Enable auto-reconnect
	MaxRetries     int           // Max reconnection attempts (0 = unlimited)
	BackoffBase    time.Duration // Initial backoff duration
	BackoffMax     time.Duration // Max backoff duration
	PingInterval   time.Duration // Ping interval for keepalive
	MessageTimeout time.Duration // Timeout for message sends
}

// DefaultConfig returns a Config with sensible defaults.
func DefaultConfig(url, agentID string) *Config {
	return &Config{
		URL:            url,
		AgentID:        agentID,
		Reconnect:      true,
		MaxRetries:     10,
		BackoffBase:    1 * time.Second,
		BackoffMax:     30 * time.Second,
		PingInterval:   30 * time.Second,
		MessageTimeout: 10 * time.Second,
	}
}
