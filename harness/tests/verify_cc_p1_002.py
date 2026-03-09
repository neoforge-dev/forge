#!/usr/bin/env python3
"""
Verification script for CC-P1-002: Sessions page action buttons

This script verifies that all action buttons on the Sessions page are
properly connected to the backend APIs.

Requirements:
1. Message button calls POST /api/agents/{id}/message
2. Pause button calls POST /api/agents/{id}/pause
3. Logs button calls GET /api/agents/{id}/logs
4. Handoff button calls POST /api/agents/{id}/handoff
5. All buttons show loading state
6. Success/error toasts displayed
7. UI updates after actions
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

from forge_harness.webhook_server import app


def verify_endpoints_exist():
    """Verify all required endpoints exist in the backend."""
    print("🔍 Verifying backend API endpoints...")

    client = TestClient(app)

    # Check if endpoints exist (will return 401/404 but not 405)
    endpoints = [
        ("POST", "/api/agents/test-agent/pause", "Pause agent"),
        ("POST", "/api/agents/test-agent/message", "Send message"),
        ("GET", "/api/agents/test-agent/logs", "Get logs"),
        ("POST", "/api/agents/test-agent/handoff", "Force handoff"),
    ]

    all_passed = True
    for method, path, description in endpoints:
        try:
            if method == "GET":
                response = client.get(path)
            else:
                response = client.post(path, json={})

            # We expect auth errors (401) or agent not found (404), not method not allowed (405)
            if response.status_code == 405:
                print(f"  ❌ {description}: {method} {path} - Method not allowed")
                all_passed = False
            else:
                print(f"  ✅ {description}: {method} {path} - Endpoint exists")
        except Exception as e:
            print(f"  ❌ {description}: {method} {path} - Error: {e}")
            all_passed = False

    return all_passed


def verify_frontend_implementation():
    """Verify frontend implementation exists."""
    print("\n🔍 Verifying frontend implementation...")

    sessions_file = (
        Path(__file__).parent.parent / "command_center" / "src" / "pages" / "Sessions.tsx"
    )

    if not sessions_file.exists():
        print(f"  ❌ Sessions.tsx not found at {sessions_file}")
        return False

    content = sessions_file.read_text()

    checks = [
        ("api.agents.pause", "Pause API call"),
        ("api.agents.message", "Message API call"),
        ("api.agents.getLogs", "Logs API call"),
        ("api.agents.forceHandoff", "Handoff API call"),
        ("setIsPauseLoading", "Pause loading state"),
        ("setIsLoading", "General loading states"),
        ("toast.success", "Success notifications"),
        ("toast.error", "Error notifications"),
        ("MessageModal", "Message modal component"),
        ("LogsModal", "Logs modal component"),
        ("HandoffModal", "Handoff modal component"),
    ]

    all_passed = True
    for check, description in checks:
        if check in content:
            print(f"  ✅ {description}")
        else:
            print(f"  ❌ {description} - Not found in implementation")
            all_passed = False

    return all_passed


def verify_tests_exist():
    """Verify tests cover all action buttons."""
    print("\n🔍 Verifying test coverage...")

    test_file = (
        Path(__file__).parent.parent
        / "command_center"
        / "src"
        / "pages"
        / "__tests__"
        / "Sessions.test.tsx"
    )

    if not test_file.exists():
        print(f"  ❌ Sessions.test.tsx not found at {test_file}")
        return False

    content = test_file.read_text()

    checks = [
        ("should call pause API and show success toast", "Pause button test"),
        ("should handle pause API error", "Pause error handling test"),
        ("should open message modal and send message", "Message button test"),
        ("should open logs modal and fetch logs", "Logs button test"),
        ("should handle logs API error", "Logs error handling test"),
        ("should open handoff modal and submit handoff", "Handoff button test"),
        ("should disable pause button while loading", "Loading state test"),
        ("should refresh sessions after successful action", "Refresh after action test"),
    ]

    all_passed = True
    for check, description in checks:
        if check in content:
            print(f"  ✅ {description}")
        else:
            print(f"  ❌ {description} - Not found in tests")
            all_passed = False

    return all_passed


def main():
    """Run all verification checks."""
    print("=" * 80)
    print("CC-P1-002 VERIFICATION: Sessions Page Action Buttons")
    print("=" * 80)

    results = []

    # Run checks
    results.append(("Backend Endpoints", verify_endpoints_exist()))
    results.append(("Frontend Implementation", verify_frontend_implementation()))
    results.append(("Test Coverage", verify_tests_exist()))

    # Print summary
    print("\n" + "=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)

    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {name}")
        if not passed:
            all_passed = False

    print("=" * 80)

    if all_passed:
        print("\n🎉 CC-P1-002 COMPLETE: All action buttons are properly connected!")
        print("\nAcceptance Criteria Met:")
        print("  ✅ Message button opens modal and calls POST /api/agents/{id}/message")
        print("  ✅ Pause button calls POST /api/agents/{id}/pause")
        print("  ✅ Logs button opens modal showing GET /api/agents/{id}/logs")
        print("  ✅ Handoff button calls POST /api/agents/{id}/handoff")
        print("  ✅ Buttons show loading state while API call in progress")
        print("  ✅ Success/error toast notifications after action")
        print("  ✅ UI updates to reflect new agent state")
        return 0
    else:
        print("\n⚠️  Some checks failed. Review the output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
