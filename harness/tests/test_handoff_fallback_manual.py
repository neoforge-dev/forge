
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def test_handoff_fallback():
    # We need to mock the state store to return no handoffs from Redis
    with patch("forge_harness.webhook_server_main.get_state_store") as mock_get_state_store:
        mock_state_store = MagicMock()
        mock_state_store.is_connected.return_value = True

        mock_redis = MagicMock()
        mock_redis._client.keys.return_value = [] # No keys in Redis
        mock_state_store._redis = mock_redis

        mock_get_state_store.return_value = mock_state_store

        # Import app after patching
        from forge_harness.webhook_server_main import _get_forge_repo_root, app

        client = TestClient(app)

        # We need to mock _get_forge_repo_root to point to a temp dir where we have .forge/handoffs
        with patch("forge_harness.webhook_server_main._get_forge_repo_root") as mock_get_root:
            temp_root = Path("/tmp/forge_test_handoffs")
            temp_root.mkdir(parents=True, exist_ok=True)
            handoffs_dir = temp_root / ".forge" / "handoffs"
            handoffs_dir.mkdir(parents=True, exist_ok=True)

            # Create a test handoff file
            test_handoff = {
                "id": "ho-test-file-001",
                "from_agent": "forge:gemini",
                "to_agent": "forge:opencode",
                "task": "Test fallback to file system",
                "status": "pending",
                "priority": "high",
                "created_at": "2026-02-10T12:00:00Z"
            }
            with open(handoffs_dir / "ho-test-file-001.json", "w") as f:
                json.dump(test_handoff, f)

            mock_get_root.return_value = temp_root

            # Call the API
            # Skip auth for test
            with patch("forge_harness.webhook_server_main.verify_auth", return_value=None):
                response = client.get("/api/handoffs")

                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert len(data["data"]["handoffs"]) == 1
                assert data["data"]["handoffs"][0]["id"] == "ho-test-file-001"
                print("Fallback test passed!")

if __name__ == "__main__":
    try:
        test_handoff_fallback()
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
