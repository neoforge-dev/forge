"""
XNode SSE Listener Daemon
=========================

Subscribes to the Command Center SSE stream and processes xnode events
targeting the local node. Handles lead.send, lead.ack, lead.handoff, and
xnode.relay.exception event types with durable file-based storage.

Usage:
    listener = XNodeListener(
        command_center_url="http://prya.queue-great.ts.net:8080",
        token="your-token",
        forge_root=Path("/home/user/FORGE"),
    )
    asyncio.run(listener.start())
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger("forge_harness.xnode_listener")

_REDACTED = "[REDACTED]"
_TOKEN_ASSIGNMENT_PATTERN = re.compile(
    r"(FORGE_WEBHOOK_TOKEN\s*[=:]\s*)([^\s,;]+)",
    re.IGNORECASE,
)
_TOKEN_JSON_PATTERN = re.compile(
    r'("FORGE_WEBHOOK_TOKEN"\s*:\s*")([^"]+)(")',
    re.IGNORECASE,
)

# Event types the listener handles (direct event_type values)
HANDLED_EVENT_TYPES = frozenset(
    {
        "lead.send",
        "lead.ack",
        "lead.handoff",
        "xnode.relay.exception",
    }
)

# Channel prefixes used by the xnode bridge (e.g. "lead.prya.inbox")
# These are routed SSE event types that contain the real event_type in data.event_type
_CHANNEL_PREFIXES = ("lead.", "xnode.")

# Heartbeat interval in seconds
_HEARTBEAT_INTERVAL = 60.0

# Outbox retry interval in seconds (same cadence as heartbeat)
_OUTBOX_RETRY_INTERVAL = 60.0

# Time-to-live for outbox records: records older than this are abandoned
_OUTBOX_TTL = timedelta(hours=24)

# SSE read timeout — keep-alive pings arrive every ~15s on most servers
_SSE_READ_TIMEOUT = 90.0


class XNodeListener:
    """SSE listener daemon for cross-node realtime messaging.

    Connects to the Command Center ``/api/events`` SSE endpoint, filters events
    that target the local node, and persists them to the ``.forge/xnode/``
    directory layout consumed by the CLI and other tooling.
    """

    def __init__(
        self,
        command_center_url: str,
        token: str,
        forge_root: Path,
        hostname: str | None = None,
        channels: list[str] | None = None,
        lead_window: str | None = None,
        notify_lead: bool = True,
    ) -> None:
        """
        Args:
            command_center_url: Base URL of the Command Center (no trailing slash).
            token: Bearer token sent as ``?token=`` query parameter.
            forge_root: Path to the FORGE repository root.
            hostname: Override local hostname used for event filtering.
            channels: If set, only process events whose channel matches one of
                      these strings (e.g. ``["lead.prya.inbox"]``).  When
                      ``None`` the listener falls back to matching on the
                      envelope's ``target.node`` field.
            lead_window: tmux target for the node lead (e.g. ``forge:nova``).
                Defaults to ``forge:<hostname>``.
            notify_lead: If True, send tmux notifications to the lead window
                when messages arrive.  Set False to disable (store-only mode).
        """
        self.url = command_center_url.rstrip("/")
        self.token = token
        self.forge_root = forge_root
        self.hostname = (hostname or socket.gethostname()).strip().lower()
        self.channels = channels
        self.lead_window = lead_window or f"forge:{self.hostname}"
        self.notify_lead = notify_lead

        self._running = False
        self._backoff = 1.0
        self._max_backoff = 30.0
        self._has_tmux = shutil.which("tmux") is not None

        # Idempotency: track seen message IDs to skip replays on reconnect.
        # Bounded dict acts as an LRU — oldest entries are evicted
        # when the set exceeds _MAX_SEEN_IDS to prevent unbounded growth.
        self._seen_message_ids: dict[str, None] = {}
        self._MAX_SEEN_IDS = 10_000

        # SSE Last-Event-ID: sent on reconnect to avoid server-side replays
        self._last_event_id: str = ""

        # Ensure all storage directories exist up-front
        self._dirs = self._ensure_dirs()

    def _mark_seen(self, msg_id: str) -> None:
        """Record a message ID in the bounded LRU set.

        Evicts the oldest entries when the set exceeds ``_MAX_SEEN_IDS``.
        """
        self._seen_message_ids[msg_id] = None
        while len(self._seen_message_ids) > self._MAX_SEEN_IDS:
            self._seen_message_ids.pop(next(iter(self._seen_message_ids)))

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def _ensure_dirs(self) -> dict[str, Path]:
        """Create and return all xnode storage directories."""
        # Import here to avoid circular import; _common is CLI-layer only.
        # We duplicate the minimal logic so xnode_listener is self-contained
        # as a library module.
        root = self.forge_root / ".forge" / "xnode"
        dirs: dict[str, Path] = {
            "root": root,
            "lead_inbox": root / "lead-inbox",
            "handoffs": root / "handoffs",
            "acks": root / "acks",
            "exceptions": root / "exceptions",
            "realtime_outbox": root / "realtime-outbox",
        }
        for path in dirs.values():
            path.mkdir(parents=True, exist_ok=True)
        return dirs

    def _seed_seen_ids(self) -> None:
        """Pre-populate _seen_message_ids from existing inbox files.

        This prevents re-notifying on SSE replays after reconnect.
        """
        inbox_dir = self._dirs["lead_inbox"]
        for jsonl_file in inbox_dir.glob("*.jsonl"):
            try:
                for line in jsonl_file.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        # Handle both formats: envelope-wrapped and flat
                        msg_id = (
                            record.get("message_id")
                            or record.get("envelope", {}).get("message_id")
                        )
                        if msg_id:
                            self._mark_seen(msg_id)
                    except (json.JSONDecodeError, AttributeError):
                        continue
            except OSError:
                continue

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self, once: bool = False) -> None:
        """Start the listener loop.

        Args:
            once: If ``True``, connect, process one full batch of events until
                  the stream closes or a read timeout occurs, then exit.
                  Intended for testing and one-shot invocations.
        """
        self._running = True
        self._setup_signals()
        self._seed_seen_ids()

        logger.info(
            "XNodeListener starting — node=%s url=%s seen_ids=%d",
            self.hostname,
            self.url,
            len(self._seen_message_ids),
        )

        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        outbox_task = asyncio.create_task(self._outbox_retry_loop())

        try:
            while self._running:
                try:
                    await self._connect_and_listen()
                    # Clean exit from stream (server closed connection)
                    self._backoff = 1.0
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning(
                        "SSE connection error (backoff=%.1fs): %s",
                        self._backoff,
                        exc,
                    )

                if once or not self._running:
                    break

                # Exponential backoff before reconnect
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, self._max_backoff)
        finally:
            heartbeat_task.cancel()
            outbox_task.cancel()
            for task in (heartbeat_task, outbox_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            logger.info("XNodeListener stopped.")

    def stop(self) -> None:
        """Signal the listener to stop after the current event loop tick."""
        logger.info("XNodeListener stop requested.")
        self._running = False

    # ------------------------------------------------------------------
    # Internal: connection
    # ------------------------------------------------------------------

    async def _get_sse_session_token(self) -> str | None:
        """Exchange bearer token for a short-lived SSE session token.

        The CC rejects raw bearer tokens in the query param for non-localhost
        connections (Phase 1.3 security). This method POSTs to
        ``/api/auth/sse-session`` with the bearer token in the header and
        returns a session token for use in ``?token=``.

        Returns:
            Session token string, or None if the exchange fails (in which
            case we fall back to using the bearer token directly, which
            works for localhost/dev connections).
        """
        try:
            import aiohttp
        except ImportError:
            return None

        session_url = f"{self.url}/api/auth/sse-session"
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.post(
                    session_url,
                    headers={"Authorization": f"Bearer {self.token}"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        token = (data.get("data") or {}).get("session_token")
                        if token:
                            logger.debug("SSE session token acquired (expires in 300s)")
                            return token
                    logger.debug(
                        "SSE session token exchange returned %d, falling back to bearer",
                        resp.status,
                    )
        except Exception as exc:
            logger.debug("SSE session token exchange failed: %s", exc)
        return None

    async def _connect_and_listen(self) -> None:
        """Open an SSE connection and process events until closed."""
        try:
            import aiohttp
        except ImportError as exc:
            raise ImportError(
                "aiohttp is required for XNodeListener. Install with: uv add aiohttp"
            ) from exc

        # Exchange bearer for session token (required for non-localhost)
        sse_token = await self._get_sse_session_token() or self.token

        sse_url = f"{self.url}/api/events"
        params: dict[str, str] = {"token": sse_token}

        # Send Last-Event-ID on reconnect so the server can skip
        # already-delivered events, eliminating replay duplicates.
        if self._last_event_id:
            params["last_event_id"] = self._last_event_id

        logger.debug("Connecting to SSE: %s (last_event_id=%s)", sse_url, self._last_event_id)

        headers: dict[str, str] = {}
        if self._last_event_id:
            headers["Last-Event-ID"] = self._last_event_id

        timeout = aiohttp.ClientTimeout(
            total=None,          # No total timeout — long-lived connection
            connect=10.0,        # TCP + TLS handshake max
            sock_read=_SSE_READ_TIMEOUT,
        )
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(sse_url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    raise ConnectionError(
                        f"SSE connection rejected: HTTP {resp.status}"
                    )

                logger.info(
                    "SSE connected — node=%s status=%d last_event_id=%s",
                    self.hostname,
                    resp.status,
                    self._last_event_id,
                )
                # Reset backoff on successful connect
                self._backoff = 1.0

                # Proper SSE state machine: accumulate id/event/data fields
                # until a blank line dispatches the event.
                sse_id: str = ""
                sse_event: str = ""
                sse_data_lines: list[str] = []

                async for raw_line in resp.content:
                    if not self._running:
                        break

                    line = raw_line.decode("utf-8").rstrip("\r\n")

                    # Keep-alive comment
                    if line.startswith(":"):
                        continue

                    # Blank line = dispatch accumulated event
                    if not line:
                        if sse_data_lines:
                            data_str = "\n".join(sse_data_lines)
                            await self._dispatch_sse_event(sse_id, sse_event, data_str)
                            if sse_id:
                                self._last_event_id = sse_id
                        sse_id = ""
                        sse_event = ""
                        sse_data_lines = []
                        continue

                    # Parse SSE field: "field: value" or "field:value"
                    if ":" in line:
                        colon = line.index(":")
                        field = line[:colon]
                        value = line[colon + 1:]
                        # Remove single leading space per SSE spec
                        if value.startswith(" "):
                            value = value[1:]
                    else:
                        field = line
                        value = ""

                    if field == "data":
                        sse_data_lines.append(value)
                    elif field == "event":
                        sse_event = value
                    elif field == "id":
                        # SSE spec: ignore id fields containing null
                        if "\0" not in value:
                            sse_id = value

    async def _dispatch_sse_event(
        self, sse_id: str, sse_event: str, data_str: str
    ) -> None:
        """Process a complete SSE event after parsing id/event/data fields."""
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError as exc:
            logger.debug("SSE parse error: %s — data=%r", exc, data_str[:120])
            return

        # The SSE `event:` field (if set) is the event type from the wire.
        # Fall back to the `type` field inside the JSON payload.
        sse_type: str = sse_event or payload.get("type", "")
        data: dict[str, Any] = payload.get("data", {})

        if not sse_type:
            return

        # Resolve the canonical event_type.  The xnode bridge publishes
        # channel-routed types (e.g. "lead.prya.inbox") with the real
        # event_type inside data.event_type.  Direct publishes use the
        # SSE type directly (e.g. "lead.send").
        event_type: str = data.get("event_type", "") or sse_type

        if not self._should_process(event_type, sse_type, payload):
            return

        # Staleness check: ignore events older than 5 minutes to prevent
        # replays on SSE reconnect (CC streams historical events).
        ts_str = (
            data.get("timestamp")
            or data.get("envelope", {}).get("timestamp")
        )
        if ts_str:
            try:
                evt_time = datetime.fromisoformat(ts_str)
                if evt_time.tzinfo is None:
                    evt_time = evt_time.replace(tzinfo=UTC)
                age = datetime.now(UTC) - evt_time
                if age > timedelta(minutes=5):
                    logger.debug(
                        "Skipping stale SSE event — age=%s sse_type=%s",
                        age, sse_type,
                    )
                    return
            except (ValueError, TypeError):
                pass  # Can't parse timestamp, allow through

        # Early dedup: extract message_id from any position in the payload
        # to catch duplicates from multi-channel broadcasts (CC sends same
        # event on both direct and channel-routed SSE types).
        msg_id = (
            data.get("message_id")
            or data.get("envelope", {}).get("message_id")
        )
        if msg_id and msg_id in self._seen_message_ids:
            logger.debug(
                "Skipping duplicate SSE event — message_id=%s sse_type=%s",
                msg_id, sse_type,
            )
            return

        try:
            await self._handle_event(event_type, data)
        except Exception as exc:
            logger.error(
                "Error handling event type=%r (sse_type=%r): %s",
                event_type, sse_type, exc, exc_info=True,
            )

    # ------------------------------------------------------------------
    # Internal: filtering
    # ------------------------------------------------------------------

    def _should_process(
        self,
        event_type: str,
        sse_type: str,
        event_data: dict[str, Any],
    ) -> bool:
        """Return True if this event should be handled by the local node.

        Args:
            event_type: Canonical event type (e.g. "lead.send").
            sse_type: Raw SSE event type — may be a channel name like
                "lead.prya.inbox" when routed by the xnode bridge.
            event_data: Full parsed SSE payload.

        Filtering logic (in priority order):
        1. Reject unknown event types.
        2. If ``self.channels`` is set, check if the SSE type / channel matches.
        3. Otherwise inspect ``data.envelope.target.node`` for hostname match.
        4. Also accept events with ``data.target_node`` equal to local hostname.
        """
        # Accept if canonical event_type is known, OR if the SSE channel
        # starts with a known prefix (the bridge routes to channels like
        # "lead.prya.inbox" which carry a valid event_type in data).
        is_known = event_type in HANDLED_EVENT_TYPES
        is_channel_routed = any(sse_type.startswith(p) for p in _CHANNEL_PREFIXES)
        if not is_known and not is_channel_routed:
            return False

        data: dict[str, Any] = event_data.get("data", {})

        # Explicit channel override
        if self.channels:
            return sse_type in self.channels

        # Standard envelope shape: data.envelope.target.node
        envelope: dict[str, Any] = data.get("envelope", {})
        target: dict[str, Any] = envelope.get("target", {})
        target_node: str = (target.get("node") or "").strip().lower()

        if target_node and target_node == self.hostname:
            return True

        # Flat shape: data.target_node
        flat_node: str = (data.get("target_node") or "").strip().lower()
        if flat_node and flat_node == self.hostname:
            return True

        # Broadcast — no target node specified → process on all nodes
        if not target_node and not flat_node:
            return True

        return False

    # ------------------------------------------------------------------
    # Internal: event routing
    # ------------------------------------------------------------------

    async def _handle_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Route event to the appropriate handler method."""
        # Extract the envelope from the data payload
        envelope: dict[str, Any] = data.get("envelope", data)

        if event_type == "lead.send":
            await self._handle_lead_send(envelope)
        elif event_type == "lead.ack":
            await self._handle_lead_ack(envelope)
        elif event_type == "lead.handoff":
            await self._handle_handoff(envelope)
        elif event_type == "xnode.relay.exception":
            await self._handle_exception(envelope)
        else:
            logger.debug("No handler for event type=%r", event_type)

    async def _handle_lead_send(self, envelope: dict[str, Any]) -> None:
        """Append an incoming lead message to the node's inbox JSONL file.

        Path: ``.forge/xnode/lead-inbox/<target_node>.jsonl``

        Idempotent: skips storage and notification for already-seen message IDs.
        """
        message_id = envelope.get("message_id", "?")
        target_node = self._resolve_target_node(envelope)
        out_file = self._dirs["lead_inbox"] / f"{target_node}.jsonl"

        # Idempotency: skip if we've already processed this message
        if message_id in self._seen_message_ids:
            logger.debug("Skipping duplicate lead.send — message_id=%s", message_id)
            return
        if message_id != "?" and self._inbox_contains_message_id(out_file, message_id):
            logger.debug("Skipping lead.send already in durable inbox — message_id=%s", message_id)
            self._mark_seen(message_id)
            return
        self._mark_seen(message_id)

        record = {
            "received_at": _now_iso(),
            "type": "lead.send",
            "envelope": envelope,
        }
        _append_jsonl(out_file, record)

        task_id = envelope.get("payload", {}).get("task_id", "")
        summary = envelope.get("payload", {}).get("summary", "")
        source_node = envelope.get("source", {}).get("node", "?")
        priority = envelope.get("priority", "medium")

        logger.info(
            "lead.send stored — inbox=%s message_id=%s",
            out_file.name,
            message_id,
        )

        # Last-mile delivery: notify the lead agent in tmux (non-blocking)
        await asyncio.to_thread(
            self._notify_lead_tmux,
            "lead.send",
            task_id,
            summary,
            source_node,
            priority,
        )

    async def _handle_lead_ack(self, envelope: dict[str, Any]) -> None:
        """Write an acknowledgement record to the acks directory.

        Path: ``.forge/xnode/acks/<message_id>.json``
        """
        message_id: str = envelope.get("message_id") or envelope.get(
            "ack_message_id", _now_slug()
        )
        out_file = self._dirs["acks"] / f"{message_id}.json"
        record = {
            "received_at": _now_iso(),
            "type": "lead.ack",
            "message_id": message_id,
            "envelope": envelope,
        }
        _write_json(out_file, record)
        logger.info("lead.ack stored — file=%s", out_file.name)

        # Notify the lead agent about the acknowledgement
        ack_status = envelope.get("status", "ack")
        ack_node = envelope.get("acknowledged_by_node", "?")
        acked_msg = envelope.get("message_id", "?")
        await asyncio.to_thread(
            self._notify_lead_tmux,
            "lead.ack",
            acked_msg,
            f"{ack_status} by {ack_node}",
            ack_node,
            "low",
        )

    async def _handle_handoff(self, envelope: dict[str, Any]) -> None:
        """Write a handoff record to the handoffs directory.

        Path: ``.forge/xnode/handoffs/<handoff_id>.json``
        """
        handoff_id: str = (
            envelope.get("handoff_id")
            or envelope.get("message_id")
            or _now_slug()
        )
        out_file = self._dirs["handoffs"] / f"{handoff_id}.json"
        record = {
            "received_at": _now_iso(),
            "type": "lead.handoff",
            "handoff_id": handoff_id,
            "envelope": envelope,
        }
        _write_json(out_file, record)
        logger.info("lead.handoff stored — file=%s", out_file.name)

        # Notify the lead agent about the handoff
        source_node = envelope.get("source_node", "?")
        summary = envelope.get("summary", "")
        risk = envelope.get("risk", "medium")
        await asyncio.to_thread(
            self._notify_lead_tmux,
            "lead.handoff",
            handoff_id,
            summary,
            source_node,
            risk,
        )

    async def _handle_exception(self, envelope: dict[str, Any]) -> None:
        """Append an exception relay record to the dated JSONL file.

        Path: ``.forge/xnode/exceptions/<YYYY-MM-DD>.jsonl``
        """
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        out_file = self._dirs["exceptions"] / f"{date_str}.jsonl"
        record = {
            "received_at": _now_iso(),
            "type": "xnode.relay.exception",
            "envelope": envelope,
        }
        _append_jsonl(out_file, record)
        logger.warning(
            "xnode.relay.exception stored — file=%s message_id=%s",
            out_file.name,
            envelope.get("message_id", "?"),
        )

    # ------------------------------------------------------------------
    # Internal: heartbeat
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Periodically write heartbeat state to disk."""
        while self._running:
            await self._write_heartbeat()
            await asyncio.sleep(_HEARTBEAT_INTERVAL)

    async def _write_heartbeat(self) -> None:
        """Update the heartbeat state file.

        Path: ``.forge/ops/heartbeat/<hostname>.state.json``
        """
        heartbeat_dir = self.forge_root / ".forge" / "ops" / "heartbeat"
        heartbeat_dir.mkdir(parents=True, exist_ok=True)
        state_file = heartbeat_dir / f"{self.hostname}.state.json"

        state = {
            "listener": "xnode",
            "hostname": self.hostname,
            "last_heartbeat": _now_iso(),
            "status": "connected" if self._running else "stopped",
        }
        try:
            state_file.write_text(
                json.dumps(state, indent=2, default=str), encoding="utf-8"
            )
            logger.debug("Heartbeat written — %s", state_file)
        except OSError as exc:
            logger.warning("Failed to write heartbeat: %s", exc)

    # ------------------------------------------------------------------
    # Internal: outbox retry
    # ------------------------------------------------------------------

    async def _outbox_retry_loop(self) -> None:
        """Periodically scan the realtime-outbox and retry undelivered records."""
        while self._running:
            await self._process_outbox()
            await asyncio.sleep(_OUTBOX_RETRY_INTERVAL)

    async def _process_outbox(self) -> None:
        """Scan all JSONL files in the realtime-outbox directory and retry pending records.

        A record is pending when it has no ``delivered_at`` field.  Records
        older than ``_OUTBOX_TTL`` (24 h) are abandoned in place — the TTL
        prevents unbounded retry storms after extended downtime.
        """
        try:
            import aiohttp
        except ImportError as exc:
            logger.warning(
                "aiohttp not available — outbox retry loop disabled: %s", exc
            )
            return

        outbox_dir: Path = self._dirs["realtime_outbox"]
        jsonl_files = sorted(outbox_dir.glob("*.jsonl"))

        if not jsonl_files:
            return

        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for path in jsonl_files:
                await self._process_outbox_file(session, path)

    async def _process_outbox_file(
        self,
        session: Any,
        path: Path,
    ) -> None:
        """Process a single outbox JSONL file, retrying all pending records.

        After attempting delivery for every pending record the file is rewritten
        in-place whenever terminal state changes occur (``delivered_at`` or
        ``abandoned_at`` updates).
        """
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("Failed to read outbox file %s: %s", path.name, exc)
            return

        records: list[dict] = []
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.debug("Skipping malformed outbox line in %s: %s", path.name, exc)

        if not records:
            return

        now = datetime.now(UTC)
        now_iso = now.isoformat()
        pending_count = 0
        delivered_count = 0
        abandoned_count = 0

        for record in records:
            # Already terminal — nothing to do.
            if record.get("delivered_at") or record.get("abandoned_at"):
                continue

            pending_count += 1

            # Check TTL: abandon records older than 24 hours.
            recorded_at_raw: str = record.get("recorded_at", "")
            try:
                recorded_at = datetime.fromisoformat(recorded_at_raw)
                # Ensure timezone-aware for comparison.
                if recorded_at.tzinfo is None:
                    recorded_at = recorded_at.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                # Unparseable timestamp — abandon to avoid perpetual retries.
                logger.debug(
                    "Outbox record has unparseable recorded_at=%r — abandoning",
                    recorded_at_raw,
                )
                self._mark_outbox_abandoned(
                    record,
                    abandoned_at=now_iso,
                    reason="invalid_recorded_at",
                )
                abandoned_count += 1
                continue

            if now - recorded_at > _OUTBOX_TTL:
                self._mark_outbox_abandoned(
                    record,
                    abandoned_at=now_iso,
                    reason="expired_ttl",
                )
                abandoned_count += 1
                continue

            # Attempt delivery.
            success = await self._retry_single_record(session, record)
            if success:
                record["delivered_at"] = now_iso
                delivered_count += 1

        # Rewrite the file with updated records if anything changed.
        if delivered_count > 0 or abandoned_count > 0:
            self._rewrite_jsonl(path, records)

        if pending_count > 0:
            remaining_count = max(pending_count - delivered_count - abandoned_count, 0)
            logger.info(
                "Outbox retry: delivered=%d abandoned=%d pending=%d total=%d",
                delivered_count,
                abandoned_count,
                remaining_count,
                pending_count,
            )
        if abandoned_count > 0:
            logger.info(
                "Outbox retry: abandoned %d stale/malformed records",
                abandoned_count,
            )

    def _mark_outbox_abandoned(
        self,
        record: dict,
        *,
        abandoned_at: str,
        reason: str,
    ) -> None:
        """Mark an outbox record as terminally abandoned."""
        if record.get("abandoned_at"):
            return
        record["abandoned_at"] = abandoned_at
        record["abandon_reason"] = reason

    async def _retry_single_record(
        self,
        session: Any,
        record: dict,
    ) -> bool:
        """Attempt to deliver a single outbox record. Returns True on success."""
        url = f"{self.url}/api/xnode/events"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        payload = {
            "event_type": record.get("event_type", "unknown"),
            "envelope": record.get("envelope", {}),
        }
        try:
            import aiohttp

            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return 200 <= resp.status < 300
        except Exception as e:
            logger.warning("Outbox retry failed: %s", e)
            return False

    def _rewrite_jsonl(self, path: Path, records: list[dict]) -> None:
        """Atomically rewrite a JSONL file with updated records."""
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(_redact_secrets(record), default=str) + "\n")
        os.replace(tmp, path)

    # ------------------------------------------------------------------
    # Internal: signal handling
    # ------------------------------------------------------------------

    def _setup_signals(self) -> None:
        """Register SIGINT / SIGTERM handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()

        def _handle_signal(sig: signal.Signals) -> None:
            logger.info("Received signal %s — shutting down.", sig.name)
            self.stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handle_signal, sig)
            except (NotImplementedError, ValueError):
                # Windows / certain environments don't support add_signal_handler
                pass

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _resolve_target_node(self, envelope: dict[str, Any]) -> str:
        """Extract the target node name from an envelope, falling back to self."""
        target: dict[str, Any] = envelope.get("target", {})
        node = (target.get("node") or envelope.get("target_node") or self.hostname)
        return str(node).strip().lower() or self.hostname

    def _inbox_contains_message_id(self, path: Path, message_id: str) -> bool:
        """Return True when inbox JSONL already contains the given message ID."""
        if not path.exists():
            return False
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                row = line.strip()
                if not row:
                    continue
                try:
                    parsed = json.loads(row)
                except json.JSONDecodeError:
                    continue
                existing_id = parsed.get("message_id") or parsed.get("envelope", {}).get("message_id")
                if existing_id == message_id:
                    return True
        except OSError:
            return False
        return False

    # ------------------------------------------------------------------
    # Internal: last-mile lead notification
    # ------------------------------------------------------------------

    # Lock to prevent concurrent tmux send-keys from interleaving
    _tmux_send_lock = threading.Lock()

    def _notify_lead_tmux(
        self,
        event_type: str,
        task_id: str = "",
        summary: str = "",
        source_node: str = "",
        priority: str = "medium",
    ) -> None:
        """Send a short notification to the lead agent's tmux window.

        This is the last-mile delivery that bridges the gap between the
        durable file store and the active lead agent session.  Without
        this, the lead only sees messages when it manually reads the inbox.

        Reliability pattern (matches fleet/verification.py send_to_tmux):
        1. Acquire lock to prevent concurrent sends from interleaving
        2. C-u to clear any pending input in the target pane
        3. send-keys -l (literal text, no tmux key interpretation)
        4. sleep(0.15) to let tmux process the text buffer
        5. send-keys Enter (tmux key name, separate call)
        """
        attempted = False
        send_keys_rc: int | None = None
        enter_rc: int | None = None
        final_status = "skipped"

        if not self.notify_lead or not self._has_tmux:
            final_status = "disabled" if not self.notify_lead else "tmux_unavailable"
            logger.info(
                "Lead notify result — attempted=%s send_keys_rc=%s enter_rc=%s final_status=%s window=%s task=%s",
                attempted,
                send_keys_rc,
                enter_rc,
                final_status,
                self.lead_window,
                task_id or event_type,
            )
            return

        # Build a concise notification line
        parts: list[str] = []
        if source_node:
            parts.append(f"from:{source_node}")
        if task_id:
            parts.append(task_id)
        tag = f"[XNODE {priority.upper()}]"
        budget = max(200 - len(tag) - len(" ".join(parts)) - 4, 0)
        if summary and budget > 10:
            short = summary[:budget] + ("..." if len(summary) > budget else "")
            parts.append(f"— {short}")
        message = f"{tag} {' '.join(parts)}"

        # Serialize tmux sends to prevent interleaving from duplicate SSE events
        with self._tmux_send_lock:
            try:
                session_name = self.lead_window.split(":")[0]
                result = subprocess.run(
                    ["tmux", "has-session", "-t", session_name],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode != 0:
                    logger.debug(
                        "tmux session not found for %s — skipping notification",
                        self.lead_window,
                    )
                    final_status = "session_missing"
                    logger.warning(
                        "Lead notify result — attempted=%s send_keys_rc=%s enter_rc=%s final_status=%s window=%s task=%s",
                        attempted,
                        send_keys_rc,
                        enter_rc,
                        final_status,
                        self.lead_window,
                        task_id or event_type,
                    )
                    return

                attempted = True

                # Step 1: Clear any pending input (prevents appending to stale text)
                subprocess.run(
                    ["tmux", "send-keys", "-t", self.lead_window, "C-u"],
                    capture_output=True,
                    timeout=2,
                    check=False,
                )
                time.sleep(0.05)

                # Step 2: Send literal text (-l prevents tmux key interpretation)
                send_result = subprocess.run(
                    ["tmux", "send-keys", "-t", self.lead_window, "-l", message],
                    capture_output=True,
                    timeout=5,
                )
                send_keys_rc = send_result.returncode

                # Step 3: Brief pause to let tmux process the text buffer
                time.sleep(0.15)

                # Step 4: Send Enter as tmux key name (not literal)
                enter_result = subprocess.run(
                    ["tmux", "send-keys", "-t", self.lead_window, "Enter"],
                    capture_output=True,
                    timeout=5,
                )
                enter_rc = enter_result.returncode

                if send_result.returncode != 0 or enter_result.returncode != 0:
                    final_status = "send_failed"
                    logger.warning(
                        "Lead notify result — attempted=%s send_keys_rc=%s enter_rc=%s final_status=%s window=%s task=%s",
                        attempted,
                        send_keys_rc,
                        enter_rc,
                        final_status,
                        self.lead_window,
                        task_id or event_type,
                    )
                else:
                    final_status = "delivered"
                    logger.info(
                        "Lead notify result — attempted=%s send_keys_rc=%s enter_rc=%s final_status=%s window=%s task=%s",
                        attempted,
                        send_keys_rc,
                        enter_rc,
                        final_status,
                        self.lead_window,
                        task_id or event_type,
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                final_status = "error"
                logger.warning(
                    "Lead notify result — attempted=%s send_keys_rc=%s enter_rc=%s final_status=%s window=%s task=%s error=%s",
                    attempted,
                    send_keys_rc,
                    enter_rc,
                    final_status,
                    self.lead_window,
                    task_id or event_type,
                    exc,
                )


# ---------------------------------------------------------------------------
# Module-level helpers (no external dependencies)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


def _now_slug() -> str:
    """Return a short timestamp slug suitable for filenames."""
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """Append a JSON record as a single line to a JSONL file."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_redact_secrets(payload), default=str) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON record to a file, overwriting if it already exists."""
    path.write_text(
        json.dumps(_redact_secrets(payload), indent=2, default=str),
        encoding="utf-8",
    )


def _redact_secrets(value: Any) -> Any:
    """Return a deep copy with known secret patterns redacted."""
    if isinstance(value, dict):
        return {k: _redact_secrets(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, str):
        redacted = _TOKEN_ASSIGNMENT_PATTERN.sub(rf"\1{_REDACTED}", value)
        redacted = _TOKEN_JSON_PATTERN.sub(rf"\1{_REDACTED}\3", redacted)
        return redacted
    return value
