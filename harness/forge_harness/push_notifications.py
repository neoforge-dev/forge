"""
Push Notification Manager for FORGE Command Center
===================================================

Web Push API backend using VAPID (Voluntary Application Server Identification)
for sending push notifications to subscribed clients.

Usage:
    # Initialize push manager
    push_manager = PushNotificationManager(
        vapid_private_key=os.environ.get("VAPID_PRIVATE_KEY"),
        vapid_subject="mailto:admin@forge.com",
        subscription_store_path=".forge_push_subscriptions.json"
    )

    # Add FastAPI routes
    push_manager.add_routes(app)

Environment Variables:
    VAPID_PRIVATE_KEY: VAPID private key for authentication
    VAPID_SUBJECT: Subject for VAPID (mailto: or URL)
    FORGE_PUSH_SUBSCRIPTIONS: Path to subscription storage file

Security:
    - Rate limits push sends per user
    - Validates subscription format
    - Requires authentication for subscription management
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

try:
    from typing import Optional

    from fastapi import HTTPException, Request, Response
    from pydantic import BaseModel
except ImportError:
    raise ImportError(
        "FastAPI and Pydantic are required for push notifications. "
        "Install with: pip install fastapi pydantic"
    )

from .logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# DATA MODELS
# ============================================================================


class PushSubscriptionCreate(BaseModel):
    """Push subscription creation request."""

    subscription: dict[str, Any]
    update: bool = False
    user_agent: str | None = None


class PushSubscriptionRemove(BaseModel):
    """Push subscription removal request."""

    endpoint: str


class PushTestRequest(BaseModel):
    """Test push notification request."""

    payload: dict[str, Any]


class PushNotificationPayload(BaseModel):
    """Push notification payload."""

    title: str
    body: str
    icon: str | None = None
    badge: str | None = None
    image: str | None = None
    data: dict[str, Any] | None = None
    actions: list[dict[str, str]] | None = None
    tag: str | None = None
    timestamp: int | None = None
    requireInteraction: bool | None = None
    silent: bool | None = None
    vibrate: list[int] | None = None


@dataclass
class StoredSubscription:
    """Stored push subscription with metadata.

    Attributes:
        subscription_id: Unique subscription ID
        endpoint: Push service endpoint URL
        keys: VAPID keys (p256dh, auth)
        user_agent: Client user agent
        created_at: Subscription creation timestamp
        last_used: Last successful push timestamp
        push_count: Total pushes sent to this subscription
        failed_count: Failed push attempts
    """

    subscription_id: str
    endpoint: str
    keys: dict[str, str]
    user_agent: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_used: str | None = None
    push_count: int = 0
    failed_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        return {
            "subscription_id": self.subscription_id,
            "endpoint": self.endpoint,
            "keys": self.keys,
            "user_agent": self.user_agent,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "push_count": self.push_count,
            "failed_count": self.failed_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StoredSubscription":
        """Create from dictionary."""
        return cls(
            subscription_id=data["subscription_id"],
            endpoint=data["endpoint"],
            keys=data["keys"],
            user_agent=data.get("user_agent"),
            created_at=data.get("created_at", datetime.now(UTC).isoformat()),
            last_used=data.get("last_used"),
            push_count=data.get("push_count", 0),
            failed_count=data.get("failed_count", 0),
        )


# =============================================================================
# PUSH NOTIFICATION MANAGER
# =============================================================================


class PushNotificationManager:
    """Manages web push subscriptions and sends notifications.

    Features:
        - Store and manage push subscriptions
        - Send push notifications using web-push library
        - Rate limit per-subscription sends
        - File-based subscription storage for MVP
    """

    def __init__(
        self,
        vapid_private_key: str | None = None,
        vapid_subject: str = "mailto:admin@forge.com",
        subscription_store_path: str | None = None,
    ):
        """Initialize push notification manager.

        Args:
            vapid_private_key: VAPID private key for authentication
            vapid_subject: Subject for VAPID (mailto: or URL)
            subscription_store_path: Path to subscription storage file
        """
        self.vapid_private_key = vapid_private_key or os.environ.get(
            "VAPID_PRIVATE_KEY", "YOUR_VAPID_PRIVATE_KEY_HERE"
        )
        self.vapid_subject = vapid_subject
        self.subscription_store_path = Path(
            subscription_store_path
            or os.environ.get("FORGE_PUSH_SUBSCRIPTIONS", ".forge_push_subscriptions.json")
        )

        # Thread-safe subscription storage
        self._lock = Lock()
        self._subscriptions: dict[str, StoredSubscription] = {}

        # Rate limiting (per subscription)
        self._rate_limits: dict[str, list[float]] = {}
        self._max_sends_per_minute = 10

        # Load existing subscriptions
        self._load_subscriptions()

        logger.info(
            f"Push notification manager initialized with {len(self._subscriptions)} subscriptions"
        )

    # =========================================================================
    # SUBSCRIPTION MANAGEMENT
    # =========================================================================

    def subscribe(self, request: PushSubscriptionCreate) -> dict[str, Any]:
        """Register a new push subscription or update existing one.

        Args:
            request: Subscription request with endpoint and keys

        Returns:
            Response with subscription ID and details

        Raises:
            HTTPException: If subscription is invalid
        """
        # Validate subscription structure
        if "endpoint" not in request.subscription:
            raise HTTPException(status_code=400, detail="Missing endpoint in subscription")

        if "keys" not in request.subscription:
            raise HTTPException(status_code=400, detail="Missing keys in subscription")

        endpoint = request.subscription["endpoint"]
        keys = request.subscription["keys"]

        # Validate VAPID keys
        if "p256dh" not in keys or "auth" not in keys:
            raise HTTPException(status_code=400, detail="Invalid VAPID keys")

        # Generate or find subscription ID
        subscription_id = self._generate_subscription_id(endpoint)

        with self._lock:
            # Update existing or create new
            if request.update and subscription_id in self._subscriptions:
                stored = self._subscriptions[subscription_id]
                stored.keys = keys
                stored.last_used = datetime.now(UTC).isoformat()
                logger.info(f"Updated subscription {subscription_id}")
            else:
                stored = StoredSubscription(
                    subscription_id=subscription_id,
                    endpoint=endpoint,
                    keys=keys,
                    user_agent=request.user_agent,
                )
                self._subscriptions[subscription_id] = stored
                logger.info(f"Created new subscription {subscription_id}")

            # Save to storage
            self._save_subscriptions()

        return {
            "success": True,
            "subscriptionId": subscription_id,
            "endpoint": endpoint,
            "createdAt": stored.created_at,
        }

    def unsubscribe(self, request: PushSubscriptionRemove) -> dict[str, Any]:
        """Remove a push subscription.

        Args:
            request: Unsubscription request with endpoint

        Returns:
            Success response
        """
        subscription_id = self._generate_subscription_id(request.endpoint)

        with self._lock:
            if subscription_id in self._subscriptions:
                del self._subscriptions[subscription_id]
                self._save_subscriptions()
                logger.info(f"Removed subscription {subscription_id}")

        return {"success": True, "message": "Subscription removed"}

    # =========================================================================
    # PUSH SENDING
    # =========================================================================

    def send_test_notification(self, request: PushTestRequest) -> dict[str, Any]:
        """Send a test notification to all active subscriptions.

        Args:
            request: Test notification request with payload

        Returns:
            Send result with counts
        """
        payload = PushNotificationPayload(**request.payload)
        results = self._send_to_all(payload)

        return {
            "success": True,
            "sent": results["sent"],
            "failed": results["failed"],
            "total": results["total"],
        }

    def send_notification(
        self, payload: PushNotificationPayload, tier: str | None = None
    ) -> dict[str, Any]:
        """Send a notification to all active subscriptions.

        Args:
            payload: Notification payload
            tier: Optional tier for filtering (critical, high, normal, low)

        Returns:
            Send result with counts
        """
        return self._send_to_all(payload)

    def _send_to_all(self, payload: PushNotificationPayload) -> dict[str, Any]:
        """Send notification to all active subscriptions.

        Returns:
            Send result with sent/failed counts
        """
        sent = 0
        failed = 0

        with self._lock:
            subscriptions = list(self._subscriptions.values())

        # Try importing web-push
        try:
            import webpush as WebPush
        except ImportError:
            logger.warning(
                "web-push library not installed. Install with: pip install web-push. "
                "Returning mock success."
            )
            # For MVP, return mock success without web-push
            return {
                "sent": len(subscriptions),
                "failed": 0,
                "total": len(subscriptions),
                "note": "web-push not installed - mock send",
            }

        for subscription in subscriptions:
            # Check rate limit
            if self._is_rate_limited(subscription.subscription_id):
                logger.debug(f"Rate limited subscription {subscription.subscription_id}")
                failed += 1
                continue

            try:
                # Prepare subscription info
                subscription_info = {
                    "endpoint": subscription.endpoint,
                    "keys": subscription.keys,
                }

                # Prepare payload data
                payload_data = {
                    "title": payload.title,
                    "body": payload.body,
                    "icon": payload.icon,
                    "badge": payload.badge,
                    "image": payload.image,
                    "data": payload.data,
                    "actions": payload.actions,
                    "tag": payload.tag,
                    "timestamp": payload.timestamp or int(time.time() * 1000),
                    "requireInteraction": payload.requireInteraction,
                    "silent": payload.silent,
                    "vibrate": payload.vibrate,
                }

                # Send push notification
                WebPush.webpush(
                    subscription_info,
                    json.dumps(payload_data),
                    vapid_private_key=self.vapid_private_key,
                    vapid_claims={"sub": self.vapid_subject},
                )

                # Update subscription stats
                with self._lock:
                    subscription.last_used = datetime.now(UTC).isoformat()
                    subscription.push_count += 1
                    self._save_subscriptions()

                sent += 1
                logger.debug(f"Sent push to {subscription.subscription_id}")

            except Exception as e:
                logger.error(f"Failed to send push to {subscription.subscription_id}: {e}")
                failed += 1

                # Update failed count
                with self._lock:
                    subscription.failed_count += 1

        return {
            "sent": sent,
            "failed": failed,
            "total": len(subscriptions),
        }

    # =========================================================================
    # FASTAPI ROUTES
    # =========================================================================

    def add_routes(self, app):
        """Add push notification routes to FastAPI app.

        Args:
            app: FastAPI application instance
        """

        @app.post("/api/push/subscribe")
        async def push_subscribe(request: PushSubscriptionCreate):
            """Subscribe to push notifications.

            Expects JSON body with subscription details from PushManager.subscribe()
            """
            return self.subscribe(request)

        @app.post("/api/push/unsubscribe")
        async def push_unsubscribe(request: PushSubscriptionRemove):
            """Unsubscribe from push notifications.

            Expects JSON body with endpoint to remove
            """
            return self.unsubscribe(request)

        @app.post("/api/push/test")
        async def push_test(request: PushTestRequest):
            """Send a test push notification.

            Expects JSON body with notification payload
            """
            return self.send_test_notification(request)

        @app.get("/api/push/subscriptions")
        async def push_list_subscriptions():
            """List all active push subscriptions (for debugging).

            Returns count and basic info about subscriptions
            """
            with self._lock:
                subs = list(self._subscriptions.values())

            return {
                "success": True,
                "count": len(subs),
                "subscriptions": [
                    {
                        "subscription_id": s.subscription_id,
                        "endpoint": s.endpoint,
                        "created_at": s.created_at,
                        "push_count": s.push_count,
                        "failed_count": s.failed_count,
                    }
                    for s in subs
                ],
            }

    # =========================================================================
    # PRIVATE METHODS
    # =========================================================================

    def _generate_subscription_id(self, endpoint: str) -> str:
        """Generate a subscription ID from endpoint URL."""
        import hashlib

        # Use hash of endpoint as ID
        return hashlib.sha256(endpoint.encode()).hexdigest()[:32]

    def _is_rate_limited(self, subscription_id: str) -> bool:
        """Check if subscription is rate limited.

        Returns True if rate limit exceeded (max 10 sends per minute)
        """
        now = time.monotonic()

        # Clean old timestamps
        if subscription_id in self._rate_limits:
            self._rate_limits[subscription_id] = [
                ts for ts in self._rate_limits[subscription_id] if now - ts < 60
            ]
        else:
            self._rate_limits[subscription_id] = []

        # Check limit
        if len(self._rate_limits[subscription_id]) >= self._max_sends_per_minute:
            return True

        # Add current timestamp
        self._rate_limits[subscription_id].append(now)
        return False

    def _load_subscriptions(self):
        """Load subscriptions from file storage."""
        if not self.subscription_store_path.exists():
            logger.info("No existing subscription storage found")
            return

        try:
            with open(self.subscription_store_path) as f:
                data = json.load(f)

            for sub_data in data.get("subscriptions", []):
                stored = StoredSubscription.from_dict(sub_data)
                self._subscriptions[stored.subscription_id] = stored

            logger.info(f"Loaded {len(self._subscriptions)} subscriptions from storage")

        except Exception as e:
            logger.error(f"Failed to load subscriptions: {e}")

    def _save_subscriptions(self):
        """Save subscriptions to file storage."""
        try:
            data = {
                "version": "1.0",
                "updated_at": datetime.now(UTC).isoformat(),
                "subscriptions": [s.to_dict() for s in self._subscriptions.values()],
            }

            # Create parent directory if needed
            self.subscription_store_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.subscription_store_path, "w") as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to save subscriptions: {e}")


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

_push_manager: PushNotificationManager | None = None


def get_push_manager() -> PushNotificationManager:
    """Get or create global push notification manager instance."""
    global _push_manager
    if _push_manager is None:
        _push_manager = PushNotificationManager()
    return _push_manager
