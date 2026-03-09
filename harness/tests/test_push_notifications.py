"""
Tests for Push Notification Manager
====================================

Comprehensive tests for forge_harness.push_notifications module.
"""

import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


class TestPushSubscriptionCreate:
    """Tests for PushSubscriptionCreate model."""

    def test_subscription_create(self):
        """Test PushSubscriptionCreate creation."""
        from forge_harness.push_notifications import PushSubscriptionCreate

        request = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "BKey",
                    "auth": "AKey",
                },
            },
            update=False,
            user_agent="Mozilla/5.0",
        )
        assert request.subscription["endpoint"] == "https://fcm.googleapis.com/fcm/send/abc123"
        assert request.subscription["keys"]["p256dh"] == "BKey"
        assert request.update is False
        assert request.user_agent == "Mozilla/5.0"

    def test_subscription_create_defaults(self):
        """Test PushSubscriptionCreate with defaults."""
        from forge_harness.push_notifications import PushSubscriptionCreate

        request = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://updates.push.services.mozilla.com/wpush/v1/xyz",
                "keys": {
                    "p256dh": "BKey",
                    "auth": "AKey",
                },
            }
        )
        assert request.update is False
        assert request.user_agent is None


class TestPushSubscriptionRemove:
    """Tests for PushSubscriptionRemove model."""

    def test_subscription_remove(self):
        """Test PushSubscriptionRemove creation."""
        from forge_harness.push_notifications import PushSubscriptionRemove

        request = PushSubscriptionRemove(endpoint="https://fcm.googleapis.com/fcm/send/abc123")
        assert request.endpoint == "https://fcm.googleapis.com/fcm/send/abc123"


class TestPushNotificationPayload:
    """Tests for PushNotificationPayload model."""

    def test_payload_minimal(self):
        """Test minimal payload creation."""
        from forge_harness.push_notifications import PushNotificationPayload

        payload = PushNotificationPayload(
            title="Test Notification",
            body="This is a test",
        )
        assert payload.title == "Test Notification"
        assert payload.body == "This is a test"
        assert payload.icon is None
        assert payload.badge is None

    def test_payload_full(self):
        """Test full payload with all fields."""
        from forge_harness.push_notifications import PushNotificationPayload

        payload = PushNotificationPayload(
            title="Feature Complete",
            body="Voice Coach authentication is ready",
            icon="/icon.png",
            badge="/badge.png",
            image="/screenshot.png",
            data={"feature_id": "VC-042"},
            actions=[
                {"action": "approve", "title": "Approve"},
                {"action": "reject", "title": "Reject"},
            ],
            tag="feature-complete",
            timestamp=1234567890000,
            requireInteraction=True,
            silent=False,
            vibrate=[200, 100, 200],
        )
        assert payload.title == "Feature Complete"
        assert payload.data == {"feature_id": "VC-042"}
        assert len(payload.actions) == 2
        assert payload.requireInteraction is True
        assert payload.vibrate == [200, 100, 200]


class TestStoredSubscription:
    """Tests for StoredSubscription dataclass."""

    def test_subscription_creation(self):
        """Test StoredSubscription creation."""
        from forge_harness.push_notifications import StoredSubscription

        sub = StoredSubscription(
            subscription_id="abc123",
            endpoint="https://fcm.googleapis.com/fcm/send/xyz",
            keys={"p256dh": "BKey", "auth": "AKey"},
            user_agent="Mozilla/5.0",
        )
        assert sub.subscription_id == "abc123"
        assert sub.endpoint == "https://fcm.googleapis.com/fcm/send/xyz"
        assert sub.push_count == 0
        assert sub.failed_count == 0
        assert sub.created_at is not None

    def test_subscription_to_dict(self):
        """Test StoredSubscription to_dict conversion."""
        from forge_harness.push_notifications import StoredSubscription

        sub = StoredSubscription(
            subscription_id="abc123",
            endpoint="https://fcm.googleapis.com/fcm/send/xyz",
            keys={"p256dh": "BKey", "auth": "AKey"},
        )
        data = sub.to_dict()
        assert data["subscription_id"] == "abc123"
        assert data["endpoint"] == "https://fcm.googleapis.com/fcm/send/xyz"
        assert data["keys"]["p256dh"] == "BKey"
        assert data["push_count"] == 0

    def test_subscription_from_dict(self):
        """Test StoredSubscription from_dict creation."""
        from forge_harness.push_notifications import StoredSubscription

        data = {
            "subscription_id": "xyz789",
            "endpoint": "https://updates.push.services.mozilla.com/wpush/v1/abc",
            "keys": {"p256dh": "BKey", "auth": "AKey"},
            "user_agent": "Chrome/100.0",
            "push_count": 5,
            "failed_count": 1,
        }
        sub = StoredSubscription.from_dict(data)
        assert sub.subscription_id == "xyz789"
        assert sub.push_count == 5
        assert sub.failed_count == 1
        assert sub.user_agent == "Chrome/100.0"


class TestPushNotificationManager:
    """Tests for PushNotificationManager class."""

    @pytest.fixture
    def temp_storage(self, tmp_path):
        """Create temporary storage file path."""
        return tmp_path / "test_subscriptions.json"

    @pytest.fixture
    def mock_manager(self, temp_storage):
        """Create PushNotificationManager with test storage."""
        from forge_harness.push_notifications import PushNotificationManager

        return PushNotificationManager(
            vapid_private_key="test_vapid_key",
            vapid_subject="mailto:test@forge.com",
            subscription_store_path=str(temp_storage),
        )

    def test_manager_initialization(self, mock_manager):
        """Test PushNotificationManager initialization."""
        assert mock_manager.vapid_private_key == "test_vapid_key"
        assert mock_manager.vapid_subject == "mailto:test@forge.com"
        assert len(mock_manager._subscriptions) == 0

    def test_manager_subscribe_new(self, mock_manager):
        """Test subscribing a new push subscription."""
        from forge_harness.push_notifications import PushSubscriptionCreate

        request = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "BKey123",
                    "auth": "AKey123",
                },
            },
            user_agent="Mozilla/5.0",
        )
        result = mock_manager.subscribe(request)
        assert result["success"] is True
        assert "subscriptionId" in result
        assert result["endpoint"] == "https://fcm.googleapis.com/fcm/send/abc123"
        assert len(mock_manager._subscriptions) == 1

    def test_manager_subscribe_update(self, mock_manager):
        """Test updating an existing subscription."""
        from forge_harness.push_notifications import PushSubscriptionCreate

        # Subscribe first time
        request1 = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "OldKey",
                    "auth": "OldAuth",
                },
            },
        )
        result1 = mock_manager.subscribe(request1)
        sub_id = result1["subscriptionId"]

        # Update with new keys
        request2 = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "NewKey",
                    "auth": "NewAuth",
                },
            },
            update=True,
        )
        result2 = mock_manager.subscribe(request2)
        assert result2["subscriptionId"] == sub_id
        assert len(mock_manager._subscriptions) == 1

        # Verify keys were updated
        stored = mock_manager._subscriptions[sub_id]
        assert stored.keys["p256dh"] == "NewKey"

    def test_manager_subscribe_invalid_no_endpoint(self, mock_manager):
        """Test subscribing with missing endpoint fails."""
        from forge_harness.push_notifications import PushSubscriptionCreate

        request = PushSubscriptionCreate(
            subscription={
                "keys": {
                    "p256dh": "BKey",
                    "auth": "AKey",
                },
            }
        )
        with pytest.raises(Exception):  # HTTPException
            mock_manager.subscribe(request)

    def test_manager_subscribe_invalid_no_keys(self, mock_manager):
        """Test subscribing with missing keys fails."""
        from forge_harness.push_notifications import PushSubscriptionCreate

        request = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
            }
        )
        with pytest.raises(Exception):  # HTTPException
            mock_manager.subscribe(request)

    def test_manager_subscribe_invalid_keys(self, mock_manager):
        """Test subscribing with invalid VAPID keys fails."""
        from forge_harness.push_notifications import PushSubscriptionCreate

        request = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "BKey",
                    # Missing "auth" key
                },
            }
        )
        with pytest.raises(Exception):  # HTTPException
            mock_manager.subscribe(request)

    def test_manager_unsubscribe(self, mock_manager):
        """Test unsubscribing a push subscription."""
        from forge_harness.push_notifications import (
            PushSubscriptionCreate,
            PushSubscriptionRemove,
        )

        # Subscribe first
        request = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "BKey",
                    "auth": "AKey",
                },
            }
        )
        mock_manager.subscribe(request)
        assert len(mock_manager._subscriptions) == 1

        # Unsubscribe
        remove_request = PushSubscriptionRemove(
            endpoint="https://fcm.googleapis.com/fcm/send/abc123"
        )
        result = mock_manager.unsubscribe(remove_request)
        assert result["success"] is True
        assert len(mock_manager._subscriptions) == 0

    def test_manager_unsubscribe_nonexistent(self, mock_manager):
        """Test unsubscribing a non-existent subscription."""
        from forge_harness.push_notifications import PushSubscriptionRemove

        remove_request = PushSubscriptionRemove(
            endpoint="https://fcm.googleapis.com/fcm/send/nonexistent"
        )
        result = mock_manager.unsubscribe(remove_request)
        assert result["success"] is True  # Should succeed silently

    def test_manager_send_without_webpush(self, mock_manager):
        """Test sending notification without web-push library."""
        from forge_harness.push_notifications import (
            PushNotificationPayload,
            PushSubscriptionCreate,
        )

        # Add a subscription
        request = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "BKey",
                    "auth": "AKey",
                },
            }
        )
        mock_manager.subscribe(request)

        # Send notification (should return mock success without web-push)
        payload = PushNotificationPayload(
            title="Test",
            body="Test message",
        )
        result = mock_manager.send_notification(payload)
        assert result["sent"] == 1
        assert result["failed"] == 0
        assert "note" in result  # Mock send note

    def test_manager_send_with_webpush(self, mock_manager):
        """Test sending notification with web-push library."""
        from forge_harness.push_notifications import (
            PushNotificationPayload,
            PushSubscriptionCreate,
        )

        # Mock webpush module
        mock_webpush_module = Mock()
        mock_webpush = Mock()
        mock_webpush_module.webpush = mock_webpush

        # Add a subscription
        request = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "BKey",
                    "auth": "AKey",
                },
            }
        )
        mock_manager.subscribe(request)

        # Send notification
        payload = PushNotificationPayload(
            title="Feature Complete",
            body="Voice Coach ready",
            data={"feature_id": "VC-042"},
        )

        # Mock the webpush module import
        with patch.dict("sys.modules", {"webpush": mock_webpush_module}):
            result = mock_manager._send_to_all(payload)

        assert result["sent"] == 1
        assert result["failed"] == 0

        # Verify webpush was called
        assert mock_webpush.call_count == 1

    def test_manager_send_failure(self, mock_manager):
        """Test handling send failure."""
        from forge_harness.push_notifications import (
            PushNotificationPayload,
            PushSubscriptionCreate,
        )

        # Mock webpush module
        mock_webpush_module = Mock()
        mock_webpush = Mock(side_effect=Exception("Network error"))
        mock_webpush_module.webpush = mock_webpush

        # Add a subscription
        request = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "BKey",
                    "auth": "AKey",
                },
            }
        )
        result = mock_manager.subscribe(request)
        sub_id = result["subscriptionId"]

        # Send notification
        payload = PushNotificationPayload(
            title="Test",
            body="Test message",
        )

        with patch.dict("sys.modules", {"webpush": mock_webpush_module}):
            result = mock_manager._send_to_all(payload)

        assert result["sent"] == 0
        assert result["failed"] == 1

        # Verify failed count was incremented
        stored = mock_manager._subscriptions[sub_id]
        assert stored.failed_count == 1

    def test_manager_rate_limiting(self, mock_manager):
        """Test rate limiting per subscription."""
        subscription_id = "test_sub_123"

        # Should not be rate limited initially
        assert mock_manager._is_rate_limited(subscription_id) is False

        # Fill up rate limit (10 sends per minute)
        for _ in range(9):
            assert mock_manager._is_rate_limited(subscription_id) is False

        # 11th send should be rate limited
        assert mock_manager._is_rate_limited(subscription_id) is True

    def test_manager_rate_limiting_expiry(self, mock_manager):
        """Test rate limiting expires after 60 seconds."""
        subscription_id = "test_sub_456"

        # Fill up rate limit
        for _ in range(10):
            mock_manager._is_rate_limited(subscription_id)

        # Should be rate limited
        assert mock_manager._is_rate_limited(subscription_id) is True

        # Mock time advancement by modifying timestamps
        old_time = time.monotonic() - 61  # 61 seconds ago
        mock_manager._rate_limits[subscription_id] = [old_time] * 10

        # Should not be rate limited anymore
        assert mock_manager._is_rate_limited(subscription_id) is False

    def test_manager_batch_send(self, mock_manager):
        """Test sending to multiple subscriptions."""
        from forge_harness.push_notifications import (
            PushNotificationPayload,
            PushSubscriptionCreate,
        )

        # Add multiple subscriptions
        for i in range(5):
            request = PushSubscriptionCreate(
                subscription={
                    "endpoint": f"https://fcm.googleapis.com/fcm/send/sub{i}",
                    "keys": {
                        "p256dh": f"BKey{i}",
                        "auth": f"AKey{i}",
                    },
                }
            )
            mock_manager.subscribe(request)

        assert len(mock_manager._subscriptions) == 5

        # Send notification to all
        payload = PushNotificationPayload(
            title="Batch Notification",
            body="Sent to all subscribers",
        )
        result = mock_manager.send_notification(payload)
        assert result["total"] == 5
        assert result["sent"] == 5
        assert result["failed"] == 0

    def test_manager_generate_subscription_id(self, mock_manager):
        """Test subscription ID generation."""
        endpoint1 = "https://fcm.googleapis.com/fcm/send/abc123"
        endpoint2 = "https://fcm.googleapis.com/fcm/send/xyz789"

        id1 = mock_manager._generate_subscription_id(endpoint1)
        id2 = mock_manager._generate_subscription_id(endpoint2)

        # IDs should be deterministic
        assert id1 == mock_manager._generate_subscription_id(endpoint1)
        assert id2 == mock_manager._generate_subscription_id(endpoint2)

        # IDs should be different for different endpoints
        assert id1 != id2

        # IDs should be 32 characters (truncated SHA256)
        assert len(id1) == 32
        assert len(id2) == 32

    def test_manager_persistence(self, mock_manager, temp_storage):
        """Test subscription persistence to file."""
        from forge_harness.push_notifications import (
            PushNotificationManager,
            PushSubscriptionCreate,
        )

        # Add subscriptions
        request1 = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "BKey1",
                    "auth": "AKey1",
                },
            },
            user_agent="Mozilla/5.0",
        )
        request2 = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/xyz789",
                "keys": {
                    "p256dh": "BKey2",
                    "auth": "AKey2",
                },
            },
            user_agent="Chrome/100.0",
        )
        mock_manager.subscribe(request1)
        mock_manager.subscribe(request2)

        # Verify file was created
        assert temp_storage.exists()

        # Create new manager with same storage
        new_manager = PushNotificationManager(
            vapid_private_key="test_vapid_key",
            vapid_subject="mailto:test@forge.com",
            subscription_store_path=str(temp_storage),
        )

        # Verify subscriptions were loaded
        assert len(new_manager._subscriptions) == 2

    def test_manager_send_test_notification(self, mock_manager):
        """Test send_test_notification method."""
        from forge_harness.push_notifications import (
            PushSubscriptionCreate,
            PushTestRequest,
        )

        # Add a subscription
        request = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "BKey",
                    "auth": "AKey",
                },
            }
        )
        mock_manager.subscribe(request)

        # Send test notification
        test_request = PushTestRequest(
            payload={
                "title": "Test Notification",
                "body": "This is a test",
            }
        )
        result = mock_manager.send_test_notification(test_request)
        assert result["success"] is True
        assert result["total"] == 1

    def test_notification_templates(self, mock_manager):
        """Test notification payload customization."""
        from forge_harness.push_notifications import PushNotificationPayload

        # Feature completion notification
        payload1 = PushNotificationPayload(
            title="Feature Complete",
            body="Voice Coach authentication ready",
            icon="/icons/feature.png",
            badge="/icons/badge.png",
            data={"type": "feature_complete", "feature_id": "VC-042"},
            actions=[
                {"action": "review", "title": "Review"},
                {"action": "deploy", "title": "Deploy"},
            ],
            requireInteraction=True,
        )
        assert payload1.title == "Feature Complete"
        assert payload1.requireInteraction is True
        assert len(payload1.actions) == 2

        # Alert notification
        payload2 = PushNotificationPayload(
            title="Build Failed",
            body="Backend tests failing on main",
            icon="/icons/error.png",
            tag="build-alert",
            vibrate=[200, 100, 200, 100, 200],
            data={"type": "alert", "severity": "high"},
        )
        assert payload2.title == "Build Failed"
        assert payload2.vibrate == [200, 100, 200, 100, 200]

        # Silent notification
        payload3 = PushNotificationPayload(
            title="Background Sync",
            body="Data updated",
            silent=True,
            data={"type": "sync", "timestamp": 1234567890},
        )
        assert payload3.silent is True

    def test_manager_load_corrupted_storage(self, temp_storage):
        """Test handling corrupted storage file."""
        from forge_harness.push_notifications import PushNotificationManager

        # Create corrupted JSON file
        temp_storage.write_text("{ invalid json }")

        # Manager should initialize without crashing
        manager = PushNotificationManager(
            vapid_private_key="test_vapid_key",
            vapid_subject="mailto:test@forge.com",
            subscription_store_path=str(temp_storage),
        )
        assert len(manager._subscriptions) == 0


class TestPushManagerSingleton:
    """Tests for singleton push manager instance."""

    def test_get_push_manager(self):
        """Test get_push_manager singleton."""
        from forge_harness.push_notifications import get_push_manager

        manager1 = get_push_manager()
        manager2 = get_push_manager()

        # Should return same instance
        assert manager1 is manager2


class TestFastAPIIntegration:
    """Tests for FastAPI route integration."""

    @pytest.fixture
    def mock_manager(self, tmp_path):
        """Create PushNotificationManager with test storage."""
        from forge_harness.push_notifications import PushNotificationManager

        return PushNotificationManager(
            vapid_private_key="test_vapid_key",
            vapid_subject="mailto:test@forge.com",
            subscription_store_path=str(tmp_path / "subs.json"),
        )

    def test_add_routes(self, mock_manager):
        """Test adding routes to FastAPI app."""
        # Mock FastAPI app
        mock_app = Mock()
        mock_app.post = Mock()
        mock_app.get = Mock()

        mock_manager.add_routes(mock_app)

        # Verify routes were registered
        assert mock_app.post.call_count == 3  # subscribe, unsubscribe, test
        assert mock_app.get.call_count == 1  # list subscriptions

        # Verify route paths
        route_paths = [call[0][0] for call in mock_app.post.call_args_list]
        assert "/api/push/subscribe" in route_paths
        assert "/api/push/unsubscribe" in route_paths
        assert "/api/push/test" in route_paths

        get_route_paths = [call[0][0] for call in mock_app.get.call_args_list]
        assert "/api/push/subscriptions" in get_route_paths


class TestErrorHandling:
    """Tests for error handling scenarios."""

    @pytest.fixture
    def mock_manager(self, tmp_path):
        """Create manager with test storage."""
        from forge_harness.push_notifications import PushNotificationManager

        return PushNotificationManager(
            vapid_private_key="test_key",
            subscription_store_path=str(tmp_path / "subs.json"),
        )

    def test_save_subscriptions_failure(self, mock_manager):
        """Test handling save failure gracefully."""
        from forge_harness.push_notifications import PushSubscriptionCreate

        # Make storage path read-only
        mock_manager.subscription_store_path = Path("/invalid/path/subs.json")

        # Subscribe should not crash even if save fails
        request = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "BKey",
                    "auth": "AKey",
                },
            }
        )
        # Should not raise exception
        result = mock_manager.subscribe(request)
        assert result["success"] is True

    def test_send_with_rate_limit(self, mock_manager):
        """Test sending respects rate limits."""
        from forge_harness.push_notifications import (
            PushNotificationPayload,
            PushSubscriptionCreate,
        )

        # Mock webpush module
        mock_webpush_module = Mock()
        mock_webpush = Mock()
        mock_webpush_module.webpush = mock_webpush

        # Add subscription
        request = PushSubscriptionCreate(
            subscription={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                "keys": {
                    "p256dh": "BKey",
                    "auth": "AKey",
                },
            }
        )
        result = mock_manager.subscribe(request)
        sub_id = result["subscriptionId"]

        # Fill rate limit
        for _ in range(10):
            mock_manager._is_rate_limited(sub_id)

        # Send notification
        payload = PushNotificationPayload(
            title="Test",
            body="Should be rate limited",
        )

        with patch.dict("sys.modules", {"webpush": mock_webpush_module}):
            result = mock_manager._send_to_all(payload)

        # Should fail due to rate limit
        assert result["failed"] == 1
        assert result["sent"] == 0


class TestNotificationCustomization:
    """Tests for notification customization and templates."""

    def test_notification_with_actions(self):
        """Test notification with action buttons."""
        from forge_harness.push_notifications import PushNotificationPayload

        payload = PushNotificationPayload(
            title="Approval Required",
            body="Deploy to production?",
            actions=[
                {"action": "approve", "title": "Approve", "icon": "/icons/check.png"},
                {"action": "reject", "title": "Reject", "icon": "/icons/x.png"},
            ],
            requireInteraction=True,
            tag="deployment-approval",
        )
        assert len(payload.actions) == 2
        assert payload.actions[0]["action"] == "approve"
        assert payload.requireInteraction is True

    def test_notification_with_image(self):
        """Test notification with image."""
        from forge_harness.push_notifications import PushNotificationPayload

        payload = PushNotificationPayload(
            title="Test Results",
            body="All tests passed",
            image="/screenshots/test-results.png",
            icon="/icons/success.png",
            badge="/icons/badge.png",
        )
        assert payload.image == "/screenshots/test-results.png"
        assert payload.icon == "/icons/success.png"

    def test_notification_vibration_pattern(self):
        """Test notification with custom vibration pattern."""
        from forge_harness.push_notifications import PushNotificationPayload

        # Urgent alert pattern
        payload = PushNotificationPayload(
            title="Critical Alert",
            body="Production server down",
            vibrate=[300, 100, 300, 100, 300],
            requireInteraction=True,
        )
        assert payload.vibrate == [300, 100, 300, 100, 300]

    def test_notification_silent(self):
        """Test silent notification for background sync."""
        from forge_harness.push_notifications import PushNotificationPayload

        payload = PushNotificationPayload(
            title="Background Sync",
            body="Data synchronized",
            silent=True,
            data={"sync_type": "incremental", "records": 150},
        )
        assert payload.silent is True
        assert payload.data["records"] == 150
