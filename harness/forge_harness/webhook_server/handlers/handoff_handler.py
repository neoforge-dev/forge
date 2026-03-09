"""Handoff Handler

Business logic for agent handoff operations.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class HandoffHandler:
    """Handles agent handoff operations for the webhook server.

    Provides Redis-based handoff storage and lifecycle management.
    """

    def __init__(self, state_store: Any = None):
        """Initialize handoff handler.

        Args:
            state_store: StateStore instance for Redis access
        """
        self.state_store = state_store
        self._ensure_state_store()

    def _ensure_state_store(self):
        """Ensure StateStore is available."""
        if self.state_store is None:
            from forge_harness.state_store import StateStore

            self.state_store = StateStore()
            self.state_store.connect()

    def _get_redis_client(self):
        """Get Redis client from StateStore."""
        if not self.state_store or not self.state_store.is_connected():
            return None
        if self.state_store.get_store_type() != "redis":
            return None
        # Access internal Redis client
        if hasattr(self.state_store, "_redis") and hasattr(self.state_store._redis, "_client"):
            return self.state_store._redis._client
        return None

    def _handoff_key(self, handoff_id: str) -> str:
        """Generate Redis key for a handoff."""
        return f"forge:handoffs:{handoff_id}"

    def _handoffs_index_key(self) -> str:
        """Generate Redis key for handoffs index."""
        return "forge:handoffs:index"

    async def list_handoffs(
        self,
        status: str | None = None,
        from_agent: str | None = None,
        to_agent: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List handoffs with optional filtering.

        Args:
            status: Filter by status (pending, accepted, rejected, completed)
            from_agent: Filter by originating agent
            to_agent: Filter by destination agent
            limit: Maximum results

        Returns:
            List of handoffs
        """
        client = self._get_redis_client()
        if not client:
            return []

        try:
            handoff_ids = client.smembers(self._handoffs_index_key())
            handoffs = []

            for handoff_id in handoff_ids:
                data = client.hgetall(self._handoff_key(handoff_id))
                if not data:
                    continue

                handoff = self._deserialize_handoff(data)

                # Apply filters
                if status and handoff["status"] != status:
                    continue
                if from_agent and handoff["from_agent"] != from_agent:
                    continue
                if to_agent and handoff["to_agent"] != to_agent:
                    continue

                handoffs.append(handoff)

            # Sort by created_at descending
            handoffs.sort(key=lambda h: h["created_at"], reverse=True)

            return handoffs[:limit]
        except Exception as e:
            logger.error(f"Failed to list handoffs: {e}")
            return []

    async def create_handoff(
        self,
        from_agent: str,
        to_agent: str,
        task_description: str,
        files: list[str] | None = None,
        priority: str = "medium",
    ) -> dict[str, Any]:
        """Create a new handoff.

        Args:
            from_agent: ID of agent giving task
            to_agent: ID of agent receiving task
            task_description: Description of work to be done
            files: List of relevant files
            priority: Handoff priority

        Returns:
            Created handoff dict
        """
        client = self._get_redis_client()
        if not client:
            raise Exception("Redis not available")

        handoff_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        handoff = {
            "id": handoff_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "task_description": task_description,
            "files": files or [],
            "priority": priority,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }

        try:
            # Store handoff
            client.hset(self._handoff_key(handoff_id), mapping=self._serialize_handoff(handoff))
            # Add to index
            client.sadd(self._handoffs_index_key(), handoff_id)
            # Set TTL (7 days)
            client.expire(self._handoff_key(handoff_id), 604800)

            return handoff
        except Exception as e:
            logger.error(f"Failed to create handoff: {e}")
            raise

    async def accept_handoff(self, handoff_id: str, accepting_agent: str | None = None) -> dict[str, Any] | None:
        """Accept a handoff."""
        return await self._update_handoff_status(handoff_id, "accepted", accepting_agent)

    async def reject_handoff(self, handoff_id: str, reason: str) -> dict[str, Any] | None:
        """Reject a handoff."""
        return await self._update_handoff_status(handoff_id, "rejected", updates={"reason": reason})

    async def complete_handoff(self, handoff_id: str, notes: str | None = None) -> dict[str, Any] | None:
        """Complete a handoff."""
        return await self._update_handoff_status(handoff_id, "completed", updates={"notes": notes})

    async def _update_handoff_status(
        self,
        handoff_id: str,
        status: str,
        accepting_agent: str | None = None,
        updates: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Internal helper to update handoff status."""
        client = self._get_redis_client()
        if not client:
            return None

        key = self._handoff_key(handoff_id)
        data = client.hgetall(key)
        if not data:
            return None

        handoff = self._deserialize_handoff(data)

        # Validate status transitions
        current_status = handoff.get("status")
        valid_transitions = {
            "accepted": ["pending"],
            "rejected": ["pending", "accepted"],
            "completed": ["accepted"],
        }
        if status in valid_transitions and current_status not in valid_transitions[status]:
            raise ValueError(
                f"Cannot transition from '{current_status}' to '{status}'"
            )

        handoff["status"] = status
        handoff["updated_at"] = datetime.now(UTC).isoformat()

        if accepting_agent:
            handoff["to_agent"] = accepting_agent

        if updates:
            handoff.update(updates)

        try:
            client.hset(key, mapping=self._serialize_handoff(handoff))
            return handoff
        except Exception as e:
            logger.error(f"Failed to update handoff {handoff_id}: {e}")
            return None

    def _serialize_handoff(self, handoff: dict[str, Any]) -> dict[str, str]:
        """Serialize handoff for Redis (all strings)."""
        return {
            k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) if v is not None else ""
            for k, v in handoff.items()
        }

    def _deserialize_handoff(self, data: dict[bytes, bytes]) -> dict[str, Any]:
        """Deserialize handoff from Redis."""
        handoff = {}
        for k, v in data.items():
            key = k.decode() if isinstance(k, bytes) else k
            value = v.decode() if isinstance(v, bytes) else v

            # Handle JSON fields
            if key in ("files",) and value:
                try:
                    handoff[key] = json.loads(value)
                except json.JSONDecodeError:
                    handoff[key] = []
            else:
                handoff[key] = value if value else None
        return handoff


# Singleton instance
_handoff_handler: HandoffHandler | None = None


def get_handoff_handler() -> HandoffHandler:
    """Get or create the handoff handler singleton."""
    global _handoff_handler
    if _handoff_handler is None:
        _handoff_handler = HandoffHandler()
    return _handoff_handler
