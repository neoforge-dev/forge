# ADR-011: WebSocket Protocol for Worker-Orchestrator Communication

**Date:** 2026-03-02
**Status:** Accepted
**Decision Makers:**
- kilo (pool-t2 agent, Architecture Review)
- cursor (pool-t2 agent, Technical Review)
- amp (pool-t2 agent, Operations Review)

---

## Context

FORGE v3 requires bidirectional communication between the Go orchestrator and Python workers. The protocol must support:

1. **Task dispatch** - Orchestrator assigns tasks to workers
2. **Status reporting** - Workers report progress and completion
3. **Heartbeat** - Health monitoring and lease renewal
4. **Reconnection** - Recovery from network interruptions
5. **Backpressure** - Prevent overwhelming slow workers

Current v2 uses file-based dispatch with tmux, which has 25% failure rate. We need a reliable, real-time protocol.

### Alternatives Considered

| Protocol | Pros | Cons | Verdict |
|----------|------|------|---------|
| **WebSocket** | Bidirectional, low latency, standard | Requires connection management | ✅ **ACCEPTED** |
| **gRPC** | High performance, streaming | Requires protobuf, harder to debug | ❌ REJECTED |
| **HTTP/2 SSE** | Server push, simple | No client→server push | ❌ REJECTED |
| **Raw TCP** | Minimal overhead | Reimplement WebSocket features | ❌ REJECTED |
| **Message Queue (Redis)** | Decoupled, durable | Additional infrastructure | ❌ REJECTED |

---

## Decision

Use **WebSocket protocol v1** for all worker-orchestrator communication.

### Protocol Design Principles

1. **Simple JSON messages** - Human-readable, easy to debug
2. **Versioned** - Protocol can evolve (v1, v2, etc.)
3. **Heartbeat-based** - Detect disconnects quickly
4. **Exponential backoff** - Graceful reconnection
5. **Bounded buffers** - Backpressure without unbounded growth

### Message Format

```go
// Common envelope for all messages
type WSMessage struct {
    Version string          `json:"v"`       // Protocol version: "1"
    Type    string          `json:"type"`    // Message type
    ID      string          `json:"id"`      // Message ID (for correlation)
    TaskID  string          `json:"task_id,omitempty"` // Associated task (if any)
    Payload json.RawMessage `json:"payload"` // Type-specific payload
    Time    time.Time       `json:"ts"`      // Timestamp
}
```

### Message Types

#### Orchestrator → Worker

```go
// Agent registration acknowledged
const MsgAgentRegisterAck = "agent.register_ack"
type AgentRegisterAckPayload struct {
    AgentID      string   `json:"agent_id"`
    Capabilities []string `json:"capabilities"`
    Config       AgentConfig `json:"config"`
}

// Task assignment
const MsgTaskAssigned = "task.assigned"
type TaskAssignedPayload struct {
    TaskID      string          `json:"task_id"`
    Type        string          `json:"type"`        // task type
    Domain      string          `json:"domain"`
    Project     string          `json:"project"`
    Payload     json.RawMessage `json:"payload"`     // Task-specific data
    PlanVersion int             `json:"plan_version"` // For magentic ledger
    LeaseID     string          `json:"lease_id"`    // Lease token
}

// Request context envelope generation
const MsgGenerateEnvelope = "generate_envelope"
type GenerateEnvelopePayload struct {
    TaskID string `json:"task_id"`
    Reason string `json:"reason"` // Why envelope needed
}

// Bootstrap worker with context envelope
const MsgBootstrap = "bootstrap"
type BootstrapPayload struct {
    TaskID   string          `json:"task_id"`
    Envelope ContextEnvelope `json:"envelope"`
}

// Heartbeat ping
const MsgPing = "ping"
type PingPayload struct {
    ServerTime time.Time `json:"server_time"`
}

// Task control messages
const MsgTaskPause = "task.pause"
const MsgTaskResume = "task.resume"
const MsgTaskCancel = "task.cancel"
```

#### Worker → Orchestrator

```go
// Agent registration
const MsgAgentRegister = "agent.register"
type AgentRegisterPayload struct {
    AgentID      string   `json:"agent_id"`
    Name         string   `json:"name"`
    Node         string   `json:"node"`
    Tier         string   `json:"tier"`         // t1, t2, t3
    Capabilities []string `json:"capabilities"` // code, test, review, etc.
    Version      string   `json:"version"`      // Worker version
}

// Task started
const MsgTaskStarted = "task.started"
type TaskStartedPayload struct {
    TaskID    string    `json:"task_id"`
    AgentID   string    `json:"agent_id"`
    StartedAt time.Time `json:"started_at"`
}

// Task completed
const MsgTaskCompleted = "task.completed"
type TaskCompletedPayload struct {
    TaskID      string          `json:"task_id"`
    AgentID     string          `json:"agent_id"`
    Result      json.RawMessage `json:"result"`
    Summary     string          `json:"summary"`      // Human-readable
    Confidence  float64         `json:"confidence"`   // 0.0-1.0
    TestsPassed bool            `json:"tests_passed"`
    CompletedAt time.Time       `json:"completed_at"`
}

// Task failed
const MsgTaskFailed = "task.failed"
type TaskFailedPayload struct {
    TaskID    string `json:"task_id"`
    AgentID   string `json:"agent_id"`
    Error     string `json:"error"`
    ErrorType string `json:"error_type"` // recoverable, permanent, timeout
    CanRetry  bool   `json:"can_retry"`
}

// Context envelope generated
const MsgEnvelopeGenerated = "envelope.generated"
type EnvelopeGeneratedPayload struct {
    TaskID   string          `json:"task_id"`
    Envelope ContextEnvelope `json:"envelope"`
}

// Heartbeat pong
const MsgPong = "pong"
type PongPayload struct {
    AgentID     string    `json:"agent_id"`
    ClientTime  time.Time `json:"client_time"`
    ServerTime  time.Time `json:"server_time"`
    ContextPct  float64   `json:"context_pct"`  // 0.0-1.0
    CurrentTask string    `json:"current_task,omitempty"`
    Status      string    `json:"status"`       // idle, busy, paused
}

// Progress update (optional, for long tasks)
const MsgTaskProgress = "task.progress"
type TaskProgressPayload struct {
    TaskID      string  `json:"task_id"`
    Percent     float64 `json:"percent"`      // 0.0-100.0
    Message     string  `json:"message"`
    ContextPct  float64 `json:"context_pct"`  // Current context usage
}
```

### Connection Lifecycle

```
┌─────────┐                    ┌─────────────┐
│  Worker │                    │ Orchestrator│
└────┬────┘                    └──────┬──────┘
     │                                │
     │────── CONNECT WebSocket ──────▶│
     │                                │
     │────── agent.register ─────────▶│
     │                                │
     │◀───── agent.register_ack ─────│
     │                                │
     │◀───── ping (every 30s) ───────│
     │────── pong ──────────────────▶│
     │                                │
     │◀───── task.assigned ──────────│
     │                                │
     │────── task.started ──────────▶│
     │                                │
     │────── task.progress ─────────▶│ (optional)
     │                                │
     │────── task.completed ────────▶│
     │         or task.failed        │
     │                                │
     │◀───── ping (every 30s) ───────│
     │────── pong ──────────────────▶│
     │                                │
     │────── DISCONNECT ────────────▶│ (or connection lost)
     │                                │
```

### Heartbeat Protocol

**Timing:**
- Orchestrator sends `ping` every **30 seconds**
- Worker must reply with `pong` within **10 seconds**
- If no `pong` received, orchestrator marks worker as **disconnected**
- Worker reconnects with exponential backoff

**Pong payload includes:**
- `context_pct` - Current context window usage (0.0-1.0)
- `current_task` - Task being executed (if any)
- `status` - idle, busy, paused

### Reconnection Strategy

```go
// Exponential backoff with jitter
func ReconnectBackoff(attempt int) time.Duration {
    base := time.Second
    max := 60 * time.Second
    
    // Exponential: 1s, 2s, 4s, 8s, 16s, 32s, 60s, 60s...
    delay := base * time.Duration(math.Pow(2, float64(attempt)))
    if delay > max {
        delay = max
    }
    
    // Add jitter (±25%)
    jitter := time.Duration(rand.Float64() * 0.5 * float64(delay))
    return delay - jitter
}
```

**Reconnection behavior:**
1. Worker detects disconnect (WebSocket close, ping timeout)
2. Wait using exponential backoff
3. Reconnect with new WebSocket
4. Send `agent.register` with same `agent_id`
5. If had active task, check lease status
6. Resume task if lease still valid, else release

### Backpressure Handling

**Orchestrator side:**
```go
const OutboundBufferSize = 100

type WorkerConnection struct {
    Send chan WSMessage  // Buffered channel
    // ...
}

func (w *WorkerConnection) SendMessage(msg WSMessage) error {
    select {
    case w.Send <- msg:
        return nil
    default:
        // Buffer full - drop non-critical messages
        if isCritical(msg.Type) {
            return ErrBufferFull
        }
        return nil  // Drop silently
    }
}

func isCritical(msgType string) bool {
    switch msgType {
    case MsgTaskAssigned, MsgTaskPause, MsgTaskCancel:
        return true
    default:
        return false
    }
}
```

**Worker side:**
- Process one task at a time in Phase 1
- No additional backpressure needed
- Future: bounded task queue per worker

### Error Handling

```go
// Protocol-level errors
const (
    ErrInvalidMessage     = "invalid_message"
    ErrUnsupportedVersion = "unsupported_version"
    ErrUnauthorized       = "unauthorized"
    ErrRateLimited        = "rate_limited"
    ErrServerError        = "server_error"
)

// Error message
type ErrorPayload struct {
    Code    string `json:"code"`
    Message string `json:"message"`
    Retry   bool   `json:"retry"` // Can client retry?
}
```

---

## Implementation

### Go (Orchestrator)

```go
package websocket

import (
    "github.com/gorilla/websocket"
    "net/http"
)

type Server struct {
    upgrader websocket.Upgrader
    hub      *Hub
}

type Hub struct {
    workers    map[string]*WorkerConn
    register   chan *WorkerConn
    unregister chan *WorkerConn
    broadcast  chan WSMessage
}

func (s *Server) HandleWebSocket(w http.ResponseWriter, r *http.Request) {
    conn, err := s.upgrader.Upgrade(w, r, nil)
    if err != nil {
        return
    }
    
    worker := &WorkerConn{
        Conn: conn,
        Send: make(chan WSMessage, 100),
    }
    
    s.hub.register <- worker
    
    // Start goroutines for read and write
    go worker.readPump()
    go worker.writePump()
}
```

### Python (Worker)

```python
import asyncio
import websockets
import json
from typing import Optional

class ForgeWorkerClient:
    def __init__(self, agent_id: str, orchestrator_url: str):
        self.agent_id = agent_id
        self.url = orchestrator_url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.current_task: Optional[str] = None
        self.reconnect_attempts = 0
        
    async def connect(self):
        """Connect with exponential backoff"""
        while True:
            try:
                self.ws = await websockets.connect(self.url)
                self.reconnect_attempts = 0
                await self._register()
                await self._handle_messages()
            except Exception as e:
                logger.error(f"Connection failed: {e}")
                await self._reconnect()
                
    async def _register(self):
        """Send agent registration"""
        await self._send({
            "v": "1",
            "type": "agent.register",
            "payload": {
                "agent_id": self.agent_id,
                "name": "kimi",
                "tier": "t2",
                "capabilities": ["code", "test"]
            }
        })
        
    async def _handle_messages(self):
        """Main message loop"""
        async for message in self.ws:
            msg = json.loads(message)
            await self._process_message(msg)
            
    async def _process_message(self, msg: dict):
        """Dispatch to handlers"""
        handlers = {
            "agent.register_ack": self._on_register_ack,
            "task.assigned": self._on_task_assigned,
            "ping": self._on_ping,
            "generate_envelope": self._on_generate_envelope,
        }
        
        handler = handlers.get(msg["type"])
        if handler:
            await handler(msg["payload"])
            
    async def _on_ping(self, payload: dict):
        """Respond to heartbeat"""
        await self._send({
            "v": "1",
            "type": "pong",
            "payload": {
                "agent_id": self.agent_id,
                "context_pct": self._get_context_pct(),
                "current_task": self.current_task,
                "status": "busy" if self.current_task else "idle"
            }
        })
```

---

## Consequences

### Positive

1. **Low latency** - Real-time bidirectional communication
2. **Standard protocol** - Well-supported libraries in Go and Python
3. **Human-readable** - JSON messages easy to debug
4. **Recoverable** - Reconnection with state recovery
5. **Observable** - Heartbeats provide health visibility

### Negative

1. **Connection state** - Must manage connection lifecycle
2. **No message durability** - Need idempotency for critical messages
3. **Firewall issues** - WebSocket may be blocked in some environments
4. **Resource usage** - Persistent connections consume memory

### Mitigations

| Risk | Mitigation |
|------|------------|
| Message loss | Idempotency keys for critical operations |
| Firewall blocking | HTTP fallback for restricted environments |
| Memory exhaustion | Connection limits, timeouts |
| Version mismatch | Protocol version negotiation |

---

## Related Decisions

- ADR-008: FORGE CLI v3 Rewrite (parent)
- ADR-010: Lease System (heartbeat integration)
- ADR-009: Agentic Patterns (task assignment)

## References

- WebSocket RFC 6455
- Gorilla WebSocket (Go): https://github.com/gorilla/websocket
- websockets (Python): https://websockets.readthedocs.io/
- FORGE CLI v3 Locked Spec: `docs/plans/FORGE_CLI_V3_LOCKED_SPECIFICATION.md`

---

**Status: ACCEPTED**

Protocol Version: 1
Heartbeat Interval: 30 seconds
Reconnection: Exponential backoff (max 60s)
Buffer Size: 100 messages
