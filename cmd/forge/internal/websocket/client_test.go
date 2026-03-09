package websocket

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// TestNewClient tests client creation.
func TestNewClient(t *testing.T) {
	tests := []struct {
		name   string
		config *Config
	}{
		{
			name:   "nil config uses defaults",
			config: nil,
		},
		{
			name: "custom config",
			config: &Config{
				URL:         "ws://localhost:8082",
				AgentID:     "test-agent",
				Reconnect:   false,
				MaxRetries:  5,
				BackoffBase: 2 * time.Second,
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			client := NewClient(tt.config)
			if client == nil {
				t.Fatal("expected non-nil client")
			}
			if client.handlers == nil {
				t.Error("expected handlers map to be initialized")
			}
		})
	}
}

// TestDefaultConfig tests default configuration.
func TestDefaultConfig(t *testing.T) {
	config := DefaultConfig("ws://localhost:8082", "test-agent")

	if config.URL != "ws://localhost:8082" {
		t.Errorf("expected URL ws://localhost:8082, got %s", config.URL)
	}
	if config.AgentID != "test-agent" {
		t.Errorf("expected AgentID test-agent, got %s", config.AgentID)
	}
	if !config.Reconnect {
		t.Error("expected Reconnect to be true")
	}
	if config.MaxRetries != 10 {
		t.Errorf("expected MaxRetries 10, got %d", config.MaxRetries)
	}
	if config.BackoffBase != 1*time.Second {
		t.Errorf("expected BackoffBase 1s, got %v", config.BackoffBase)
	}
	if config.BackoffMax != 30*time.Second {
		t.Errorf("expected BackoffMax 30s, got %v", config.BackoffMax)
	}
}

// TestCalculateBackoff tests exponential backoff calculation.
func TestCalculateBackoff(t *testing.T) {
	client := NewClient(&Config{
		BackoffBase: 1 * time.Second,
		BackoffMax:  30 * time.Second,
	})

	tests := []struct {
		retryCount int
		expected   time.Duration
	}{
		{1, 1 * time.Second},   // 1 * 2^0 = 1
		{2, 2 * time.Second},   // 1 * 2^1 = 2
		{3, 4 * time.Second},   // 1 * 2^2 = 4
		{4, 8 * time.Second},   // 1 * 2^3 = 8
		{5, 16 * time.Second},  // 1 * 2^4 = 16
		{6, 30 * time.Second},  // 1 * 2^5 = 32, capped at 30
		{10, 30 * time.Second}, // capped at max
	}

	for _, tt := range tests {
		client.retryCount = tt.retryCount
		got := client.calculateBackoff()
		if got != tt.expected {
			t.Errorf("retryCount=%d: expected %v, got %v", tt.retryCount, tt.expected, got)
		}
	}
}

// TestTelemetryJSON tests telemetry JSON serialization.
func TestTelemetryJSON(t *testing.T) {
	tel := &Telemetry{
		AgentID:    "test-agent",
		Status:     "busy",
		ContextPct: 58,
		TaskID:     "task-123",
		Node:       "prya",
	}

	data, err := json.Marshal(tel)
	if err != nil {
		t.Fatalf("failed to marshal telemetry: %v", err)
	}

	var parsed Telemetry
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatalf("failed to unmarshal telemetry: %v", err)
	}

	if parsed.AgentID != tel.AgentID {
		t.Errorf("expected AgentID %s, got %s", tel.AgentID, parsed.AgentID)
	}
	if parsed.Status != tel.Status {
		t.Errorf("expected Status %s, got %s", tel.Status, parsed.Status)
	}
	if parsed.ContextPct != tel.ContextPct {
		t.Errorf("expected ContextPct %d, got %d", tel.ContextPct, parsed.ContextPct)
	}
	if parsed.TaskID != tel.TaskID {
		t.Errorf("expected TaskID %s, got %s", tel.TaskID, parsed.TaskID)
	}
	if parsed.Node != tel.Node {
		t.Errorf("expected Node %s, got %s", tel.Node, parsed.Node)
	}
}

// TestMessageJSON tests message JSON serialization.
func TestMessageJSON(t *testing.T) {
	msg := Message{
		Type:    "telemetry",
		Payload: json.RawMessage(`{"agent_id":"test"}`),
	}

	data, err := json.Marshal(msg)
	if err != nil {
		t.Fatalf("failed to marshal message: %v", err)
	}

	var parsed Message
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatalf("failed to unmarshal message: %v", err)
	}

	if parsed.Type != msg.Type {
		t.Errorf("expected Type %s, got %s", msg.Type, parsed.Type)
	}
}

// TestEventJSON tests event JSON serialization.
func TestEventJSON(t *testing.T) {
	event := Event{
		Name: "task_dispatch",
		Data: map[string]interface{}{
			"task_id": "task-123",
		},
	}

	data, err := json.Marshal(event)
	if err != nil {
		t.Fatalf("failed to marshal event: %v", err)
	}

	var parsed Event
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatalf("failed to unmarshal event: %v", err)
	}

	if parsed.Name != event.Name {
		t.Errorf("expected Name %s, got %s", event.Name, parsed.Name)
	}
}

// TestStats tests connection statistics.
func TestStats(t *testing.T) {
	client := NewClient(&Config{
		URL:     "ws://localhost:8082",
		AgentID: "test-agent",
	})

	stats := client.Stats()

	if stats["url"] != "ws://localhost:8082" {
		t.Errorf("expected url in stats")
	}
	if stats["agent_id"] != "test-agent" {
		t.Errorf("expected agent_id in stats")
	}
	if stats["connected"] != false {
		t.Errorf("expected connected=false initially")
	}
}

// TestIsConnected tests connection state check.
func TestIsConnected(t *testing.T) {
	client := NewClient(nil)

	if client.IsConnected() {
		t.Error("expected client to be disconnected initially")
	}
}

// TestSubscribe tests handler registration.
func TestSubscribe(t *testing.T) {
	client := NewClient(nil)

	client.Subscribe("test_event", func(payload []byte) {
		// handler registered
	})

	if _, ok := client.handlers["test_event"]; !ok {
		t.Error("expected handler to be registered")
	}
}

// TestTelemetryDefaults tests that telemetry fields can have zero values.
func TestTelemetryDefaults(t *testing.T) {
	telemetry := &Telemetry{
		Status:     "idle",
		ContextPct: 25,
	}

	// Verify zero values
	if !telemetry.Timestamp.IsZero() {
		t.Error("expected zero timestamp")
	}
	if telemetry.Node != "" {
		t.Error("expected empty node")
	}
	if telemetry.AgentID != "" {
		t.Error("expected empty agent ID")
	}
}

// TestMustMarshal tests JSON marshaling helper.
func TestMustMarshal(t *testing.T) {
	data := mustMarshal(map[string]string{"foo": "bar"})
	if string(data) != `{"foo":"bar"}` {
		t.Errorf("unexpected marshal result: %s", string(data))
	}
}

// TestMustMarshalError tests that mustMarshal returns {} on marshal error.
func TestMustMarshalError(t *testing.T) {
	data := mustMarshal(make(chan int)) // channels cannot be JSON marshaled
	if string(data) != "{}" {
		t.Errorf("expected {} on marshal error, got %s", string(data))
	}
}

// TestConnectInvalidURL tests Connect with invalid URL.
func TestConnectInvalidURL(t *testing.T) {
	client := NewClient(&Config{
		URL:       "://invalid",
		AgentID:   "test",
		Reconnect: false,
	})

	ctx := context.Background()
	err := client.Connect(ctx)
	if err == nil {
		t.Fatal("expected error for invalid URL")
	}
}

// TestConnectContextCanceled tests Connect when context is canceled.
func TestConnectContextCanceled(t *testing.T) {
	client := NewClient(&Config{
		URL:       "ws://localhost:99999", // unreachable port
		AgentID:   "test",
		Reconnect: false,
	})

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	err := client.Connect(ctx)
	if err != context.Canceled {
		t.Errorf("expected context.Canceled, got %v", err)
	}
}

// wsTestServer creates an httptest server with WebSocket upgrade support.
// If closeAfter > 0, the server closes the connection after that duration
// (avoids deadlock when client.Close() waits for readLoop).
func wsTestServer(t *testing.T, closeAfter time.Duration) *httptest.Server {
	upgrader := websocket.Upgrader{}
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Logf("upgrade error: %v", err)
			return
		}
		defer conn.Close()
		if closeAfter > 0 {
			conn.SetReadDeadline(time.Now().Add(closeAfter))
		}
		for {
			_, _, err := conn.ReadMessage()
			if err != nil {
				return
			}
		}
	}))
}

// TestConnectWithMockServer tests successful connection to mock WebSocket server.
func TestConnectWithMockServer(t *testing.T) {
	// Server closes connection after 100ms to avoid deadlock on client.Close()
	srv := wsTestServer(t, 100*time.Millisecond)
	defer srv.Close()

	wsURL := "ws" + srv.URL[4:]

	client := NewClient(&Config{
		URL:       wsURL,
		AgentID:   "test-agent",
		Reconnect: false,
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	err := client.Connect(ctx)
	if err != nil {
		t.Fatalf("Connect failed: %v", err)
	}

	if !client.IsConnected() {
		t.Error("expected client to be connected")
	}

	stats := client.Stats()
	if stats["connected"] != true {
		t.Errorf("expected connected=true in stats, got %v", stats["connected"])
	}

	// Wait for server to close connection so readLoop exits before we Close()
	time.Sleep(150 * time.Millisecond)
	_ = client.Close()
}

// TestSendTelemetryNotConnected tests SendTelemetry when not connected.
func TestSendTelemetryNotConnected(t *testing.T) {
	client := NewClient(nil)

	err := client.SendTelemetry(&Telemetry{Status: "idle"})
	if err == nil {
		t.Fatal("expected error when not connected")
	}
	if err.Error() != "not connected" {
		t.Errorf("unexpected error: %v", err)
	}
}

// TestConnectSendTelemetry tests Connect + SendTelemetry with mock server.
func TestConnectSendTelemetry(t *testing.T) {
	srv := wsTestServer(t, 200*time.Millisecond)
	defer srv.Close()

	wsURL := "ws" + srv.URL[4:]

	client := NewClient(&Config{
		URL:            wsURL,
		AgentID:        "test-agent",
		Reconnect:      false,
		MessageTimeout: 5 * time.Second,
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := client.Connect(ctx); err != nil {
		t.Fatalf("Connect failed: %v", err)
	}

	tel := &Telemetry{
		Status:     "busy",
		ContextPct: 42,
		TaskID:     "task-001",
	}
	err := client.SendTelemetry(tel)
	if err != nil {
		t.Fatalf("SendTelemetry failed: %v", err)
	}

	// Verify defaults were set
	if tel.AgentID != "test-agent" {
		t.Errorf("expected AgentID to be set, got %q", tel.AgentID)
	}
	if tel.Timestamp.IsZero() {
		t.Error("expected Timestamp to be set")
	}

	time.Sleep(250 * time.Millisecond)
	_ = client.Close()
}

// TestConnectMaxRetriesExceeded tests Connect when max retries exceeded.
func TestConnectMaxRetriesExceeded(t *testing.T) {
	client := NewClient(&Config{
		URL:        "ws://localhost:99999", // unreachable
		AgentID:    "test",
		Reconnect:  true,
		MaxRetries: 2,
		BackoffBase: 1 * time.Millisecond,
		BackoffMax:  5 * time.Millisecond,
	})

	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	err := client.Connect(ctx)
	if err == nil {
		t.Fatal("expected error when max retries exceeded")
	}
	if !strings.Contains(err.Error(), "max retries exceeded") {
		t.Errorf("unexpected error: %v", err)
	}
}

// TestClose tests graceful connection close.
func TestClose(t *testing.T) {
	srv := wsTestServer(t, 100*time.Millisecond)
	defer srv.Close()

	wsURL := "ws" + srv.URL[4:]

	client := NewClient(&Config{
		URL:       wsURL,
		AgentID:   "test",
		Reconnect: false,
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := client.Connect(ctx); err != nil {
		t.Fatalf("Connect failed: %v", err)
	}

	time.Sleep(150 * time.Millisecond)
	err := client.Close()
	if err != nil {
		t.Fatalf("Close failed: %v", err)
	}

	if client.IsConnected() {
		t.Error("expected client to be disconnected after Close")
	}
}

// TestNewClientNilConfigDefaults tests that nil config gets default URL and agent ID.
func TestNewClientNilConfigDefaults(t *testing.T) {
	client := NewClient(nil)
	if client.config == nil {
		t.Fatal("expected config to be set")
	}
	if client.config.URL != "ws://localhost:8082" {
		t.Errorf("expected default URL, got %s", client.config.URL)
	}
	if client.config.AgentID != "unknown" {
		t.Errorf("expected default AgentID, got %s", client.config.AgentID)
	}
}
