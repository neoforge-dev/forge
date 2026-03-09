#!/usr/bin/env python3
"""
Manual test script for agent messaging fix.

Tests:
1. Backward compatibility with frontend's "message" field
2. Tmux agent messaging via send-keys
3. Proper error handling for non-existent agents

Usage:
    # Run the webhook server first in a separate terminal:
    cd /Users/bogdan/work/FORGE/harness
    uv run uvicorn forge_harness.webhook_server:app --port 8080

    # Then run this script:
    python tests/manual_test_agent_messaging.py
"""

import sys

import requests

# Configuration
API_BASE = "http://127.0.0.1:8080"
AUTH_TOKEN = "test_token"  # Use your actual token
HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}", "Content-Type": "application/json"}


def test_backward_compatibility():
    """Test that frontend's 'message' field works (backward compatibility)."""
    print("\n" + "=" * 60)
    print("TEST 1: Backward Compatibility (message field)")
    print("=" * 60)

    # First register a test agent
    agent_data = {
        "role": "backend-engineer",
        "name": "test-agent",
        "domain": "test-domain",
        "project": "test-project",
        "task": "Testing message field compatibility",
    }

    print("\n1. Registering test agent...")
    register_response = requests.post(
        f"{API_BASE}/api/agents/register", json=agent_data, headers=HEADERS
    )

    if register_response.status_code != 200:
        print(f"   ❌ Failed to register agent: {register_response.status_code}")
        print(f"   Response: {register_response.text}")
        return False

    agent = register_response.json()["data"]
    agent_id = agent["id"]
    print(f"   ✓ Agent registered: {agent_id}")

    # Test with 'message' field (frontend format)
    print("\n2. Sending message with 'message' field (frontend format)...")
    message_response = requests.post(
        f"{API_BASE}/api/agents/{agent_id}/message",
        json={"message": "Test message from frontend"},
        headers=HEADERS,
    )

    if message_response.status_code == 200:
        data = message_response.json()["data"]
        print("   ✓ Message sent successfully")
        print(f"   - Delivery method: {data.get('delivery_method', 'registry')}")
        print(f"   - Message: {data.get('message')}")
        return True
    else:
        print(f"   ❌ Failed to send message: {message_response.status_code}")
        print(f"   Response: {message_response.text}")
        return False


def test_tmux_agent():
    """Test sending message to tmux agent."""
    print("\n" + "=" * 60)
    print("TEST 2: Tmux Agent Messaging")
    print("=" * 60)
    print("\nNote: This test requires an actual tmux session running.")
    print("      If you don't have one, the test will gracefully fail.")

    # Try to send to a tmux agent
    print("\n1. Attempting to send message to tmux agent 'forge:tech'...")
    message_response = requests.post(
        f"{API_BASE}/api/agents/forge:tech/message",
        json={"message": "Test message to tmux agent"},
        headers=HEADERS,
    )

    if message_response.status_code == 200:
        data = message_response.json()["data"]
        print("   ✓ Message sent successfully")
        print(f"   - Delivery method: {data.get('delivery_method')}")
        print(f"   - Target: {data.get('tmux_target')}")
        return True
    elif message_response.status_code == 404:
        print("   ℹ  Tmux agent not found (expected if no tmux session)")
        return True  # Not a failure - just means no tmux session exists
    else:
        print(f"   ❌ Unexpected error: {message_response.status_code}")
        print(f"   Response: {message_response.text}")
        return False


def test_nonexistent_agent():
    """Test error handling for non-existent agent."""
    print("\n" + "=" * 60)
    print("TEST 3: Non-existent Agent Error Handling")
    print("=" * 60)

    print("\n1. Sending message to non-existent agent...")
    message_response = requests.post(
        f"{API_BASE}/api/agents/definitely-does-not-exist/message",
        json={"message": "Test"},
        headers=HEADERS,
    )

    if message_response.status_code == 404:
        print("   ✓ Correctly returned 404 for non-existent agent")
        return True
    else:
        print(f"   ❌ Expected 404, got: {message_response.status_code}")
        print(f"   Response: {message_response.text}")
        return False


def test_list_agents():
    """List all agents to see what's available."""
    print("\n" + "=" * 60)
    print("BONUS: List Available Agents")
    print("=" * 60)

    print("\n1. Fetching agent list...")
    response = requests.get(f"{API_BASE}/api/agents", headers=HEADERS)

    if response.status_code == 200:
        data = response.json()["data"]
        agents = data.get("agents", [])
        print(f"   ✓ Found {len(agents)} agents:")
        for agent in agents:
            source = agent.get("source", "unknown")
            agent_id = agent.get("id", "unknown")
            role = agent.get("role", "unknown")
            status = agent.get("status", "unknown")
            print(f"     - [{source:10}] {agent_id:20} ({role:15}) - {status}")
        return True
    else:
        print(f"   ❌ Failed to list agents: {response.status_code}")
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Agent Messaging System - Manual Test Suite")
    print("=" * 60)
    print("\nVerifying API is accessible...")

    try:
        health_response = requests.get(f"{API_BASE}/health", timeout=2)
        if health_response.status_code != 200:
            print("❌ API not healthy. Is the server running?")
            print("   Start it with: uv run uvicorn forge_harness.webhook_server:app --port 8080")
            sys.exit(1)
        print("✓ API is healthy\n")
    except requests.exceptions.RequestException as e:
        print(f"❌ Cannot connect to API at {API_BASE}")
        print(f"   Error: {e}")
        print(
            "   Start the server with: uv run uvicorn forge_harness.webhook_server:app --port 8080"
        )
        sys.exit(1)

    # Run tests
    results = []
    results.append(("Backward Compatibility", test_backward_compatibility()))
    results.append(("Tmux Agent Messaging", test_tmux_agent()))
    results.append(("Error Handling", test_nonexistent_agent()))
    results.append(("Agent List", test_list_agents()))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓ PASS" if result else "❌ FAIL"
        print(f"{status:8} {name}")

    print(f"\nResult: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed!")
        sys.exit(0)
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
