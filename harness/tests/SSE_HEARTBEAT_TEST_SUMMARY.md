# SSE Heartbeat Test Suite Summary

## Overview

Comprehensive test suite for the SSE (Server-Sent Events) heartbeat functionality that keeps WebSocket-like connections alive through proxies and load balancers.

## Test Files Created

### 1. Backend Tests: `tests/test_sse_heartbeat.py`

**Purpose**: Test the server-side SSE heartbeat implementation in `forge_harness/webhook_server.py`

**Test Coverage** (16 test cases):

#### Configuration Tests (`TestSSEHeartbeatConfiguration`)
- `test_default_heartbeat_interval` - Verifies default 30-second interval
- `test_custom_heartbeat_interval` - Tests environment variable override
- `test_heartbeat_interval_type_conversion` - Ensures proper integer conversion

#### Stream Tests (`TestSSEHeartbeatStream`)
- `test_heartbeat_event_structure` - Validates SSE event format
- `test_heartbeat_timing_with_mock_time` - Tests interval timing with mocked time
- `test_heartbeat_does_not_interfere_with_real_events` - Ensures heartbeats don't block real events
- `test_multiple_heartbeats_over_time` - Tests sustained heartbeat delivery

#### Event Structure Tests (`TestSSEEventStructure`)
- `test_sse_event_to_sse_format` - Validates SSE protocol format
- `test_heartbeat_event_id_format` - Checks event ID naming convention

#### Lifecycle Tests (`TestSSEConnectionLifecycle`)
- `test_heartbeat_starts_on_connection` - Timer initialization
- `test_heartbeat_stops_on_disconnection` - Cleanup verification
- `test_connection_survives_heartbeat_events` - Stability during heartbeats

#### EventBus Integration Tests (`TestEventBusIntegration`)
- `test_heartbeat_uses_event_bus` - Event routing verification
- `test_heartbeat_subscriber_receives_events` - Subscription mechanism

#### Integration Tests (`TestSSEHeartbeatIntegration`)
- `test_end_to_end_heartbeat_flow` - Full server-to-client flow
- `test_heartbeat_configuration_is_respected` - Config application

### 2. Frontend Tests: `command_center/src/lib/__tests__/realtime.heartbeat.test.ts`

**Purpose**: Test the client-side SSE connection manager heartbeat monitoring

**Test Coverage** (17 test cases):

#### Heartbeat Reception
- `should receive heartbeat events from server` - Basic heartbeat handling
- `should handle multiple heartbeats` - Sustained heartbeat stream
- `should treat any event as a heartbeat signal` - Real events update heartbeat

#### Timer Management
- `should start heartbeat monitoring on connection` - Timer initialization
- `should clear heartbeat timer on disconnect` - Proper cleanup
- `should maintain connection with regular heartbeats` - Timeout prevention

#### Configuration
- `should accept custom heartbeat timeout` - Config validation
- `should accept short heartbeat timeouts` - Edge case handling
- `should accept long heartbeat timeouts` - Edge case handling

#### Connection Stability
- `should handle rapid connect/disconnect cycles` - Stress testing
- `should handle multiple heartbeats in quick succession` - Burst handling
- `should ignore heartbeats after manual disconnect` - State validation

#### Event Stream Integration
- `should maintain connection with mixed events and heartbeats` - Real-world scenario
- `should maintain connection with only real events` - Alternative keepalive

#### Error Handling
- `should attempt reconnection on connection error` - Automatic recovery
- `should handle connection errors during heartbeat monitoring` - Error resilience

#### Subscription Behavior
- `should not emit heartbeat events to regular subscribers` - Internal monitoring only

## Key Implementation Details

### Backend (`webhook_server.py`)

```python
# Configuration (line 90)
SSE_HEARTBEAT_INTERVAL = int(os.environ.get("SSE_HEARTBEAT_INTERVAL", "30"))

# Event generator (lines 4096-4117)
async def event_generator():
    last_heartbeat = time.time()
    heartbeat_interval = SSE_HEARTBEAT_INTERVAL

    while True:
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            heartbeat_event = SSEEvent(
                id=f"heartbeat_{int(now)}",
                event="heartbeat",
                data={"timestamp": datetime.now(UTC).isoformat()},
                source="webhook-server",
            )
            yield heartbeat_event.to_sse_format()
            last_heartbeat = now

        # Wait for real events with 1s timeout
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        # Process real event...
```

### Frontend (`realtime.ts`)

```typescript
// Configuration (lines 160-161)
heartbeatTimeout: 60000,  // Default 60 seconds

// Heartbeat monitoring (lines 662-678)
private startHeartbeat(): void {
  this.lastHeartbeat = Date.now()

  this.heartbeatTimer = setInterval(() => {
    const now = Date.now()
    const timeSinceLastHeartbeat = now - this.lastHeartbeat

    if (timeSinceLastHeartbeat > this.config.heartbeatTimeout) {
      console.warn('[SSE] Heartbeat timeout, reconnecting...')
      this.disconnect()
      this.connect()
    }
  }, this.config.heartbeatTimeout / 2)
}

// Update on any message (lines 507, 549)
this.lastHeartbeat = Date.now()  // Called on every event
this.eventSource.addEventListener('heartbeat', (_event) => {
  updateHeartbeat()
  console.debug('[SSE] Heartbeat received')
})
```

## Test Strategy

### Fast Testing with Mocked Time
- All tests use mocked time (`vi.useFakeTimers()` for frontend, `patch('time.time')` for backend)
- No actual 30-second waits - tests complete in milliseconds
- Precise control over timing scenarios

### Mock Infrastructure
- **Backend**: Uses `TestClient` from FastAPI with temporary config
- **Frontend**: Uses `MockEventSource` class to simulate browser EventSource API
- Both disable authentication for testing (`AuthConfig(require_auth=False)`, `healthCheck: false`)

### Coverage Focus
1. **Configuration** - Environment variable handling
2. **Timing** - Heartbeat interval logic
3. **Event Structure** - SSE protocol compliance
4. **Connection Lifecycle** - Initialization and cleanup
5. **Error Handling** - Timeout detection and recovery
6. **Integration** - End-to-end flow

## Running the Tests

### Backend Tests
```bash
cd harness
uv run pytest tests/test_sse_heartbeat.py -v
```

### Frontend Tests
```bash
cd harness/command_center
npm run test -- src/lib/__tests__/realtime.heartbeat.test.ts
```

### All Tests
```bash
# Backend
cd harness && uv run pytest tests/test_sse_heartbeat.py -v

# Frontend
cd harness/command_center && npm run test -- src/lib/__tests__/realtime.heartbeat.test.ts
```

## Configuration

### Environment Variables

**Backend:**
- `SSE_HEARTBEAT_INTERVAL` - Seconds between heartbeats (default: 30)

**Frontend:**
- `heartbeatTimeout` - Milliseconds before reconnection (default: 60000)
- Check interval is `heartbeatTimeout / 2`

### Defaults
- **Server**: Sends heartbeat every 30 seconds
- **Client**: Expects heartbeat within 60 seconds
- **Client Check**: Every 30 seconds (60000ms / 2)

This provides a 2x safety margin - client waits 2x longer than server interval.

## Test Results

### Backend: 16/16 PASSING ✓
- Configuration: 3/3
- Stream: 4/4
- Event Structure: 2/2
- Lifecycle: 3/3
- EventBus: 2/2
- Integration: 2/2

### Frontend: 17/17 PASSING ✓
- Heartbeat Reception: 3/3
- Timer Management: 3/3
- Configuration: 3/3
- Connection Stability: 3/3
- Event Stream Integration: 2/2
- Error Handling: 2/2
- Subscription Behavior: 1/1

## Files Modified/Created

### Created
1. `/Users/bogdan/work/FORGE/harness/tests/test_sse_heartbeat.py` (545 lines)
2. `/Users/bogdan/work/FORGE/harness/command_center/src/lib/__tests__/realtime.heartbeat.test.ts` (495 lines)
3. `/Users/bogdan/work/FORGE/harness/tests/SSE_HEARTBEAT_TEST_SUMMARY.md` (this file)

### Existing Implementation (No Changes)
- `/Users/bogdan/work/FORGE/harness/forge_harness/webhook_server.py` (SSE_HEARTBEAT_INTERVAL, lines 90, 4096-4117)
- `/Users/bogdan/work/FORGE/harness/command_center/src/lib/realtime.ts` (heartbeat handling, lines 160-161, 549, 662-678)

## Quality Metrics

- **Total Test Cases**: 33 (16 backend + 17 frontend)
- **Test Execution Time**: <1 second (all use mocked time)
- **Mock Coverage**: 100% (no real network calls, no real time waits)
- **Assertion Density**: High (multiple assertions per test)
- **Edge Cases Covered**: Timeouts, rapid connections, errors, mixed events

## Future Enhancements

Potential additions to the test suite:
1. **Performance Tests** - Measure heartbeat overhead
2. **Concurrency Tests** - Multiple simultaneous connections
3. **Network Simulation** - Packet loss, latency
4. **Browser Compatibility** - Different EventSource implementations
5. **Load Tests** - Many clients, many heartbeats

## Related Documentation

- [SSE Protocol Specification](https://html.spec.whatwg.org/multipage/server-sent-events.html)
- [EventSource MDN Docs](https://developer.mozilla.org/en-US/docs/Web/API/EventSource)
- [FastAPI StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse)
